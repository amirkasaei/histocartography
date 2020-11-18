import argparse
import datetime
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import dgl
import mlflow
import torch
import yaml
from torch.utils.data import DataLoader
from tqdm.auto import trange

from logging_helper import GraphClassificationLoggingHelper, log_parameters, log_sources
from models import WeakTissueClassifier
from utils import dynamic_import_from, fix_seeds, start_logging


def collate(
    samples: List[Tuple[dgl.DGLGraph, torch.Tensor, torch.Tensor]]
) -> Tuple[dgl.DGLGraph, torch.Tensor, torch.Tensor]:
    """Aggregate a batch by performing the following:
       Create a graph with disconnected components using dgl.batch
       Stack the graph labels one-hot encoded labels (to shape B x nr_classes)
       Concatenate the node labels to a single vector (graph association can be read from graph.batch_num_nodes)

    Args:
        samples (List[Tuple[dgl.DGLGraph, torch.Tensor, torch.Tensor]]): List of unaggregated samples

    Returns:
        Tuple[dgl.DGLGraph, torch.Tensor, torch.Tensor]: Aggregated graph and labels
    """
    graphs, graph_labels, node_labels = map(list, zip(*samples))
    return dgl.batch(graphs), torch.stack(graph_labels), torch.cat(node_labels)


def get_loss(config, name, device):
    loss_config = config[name]
    loss_class = dynamic_import_from("losses", loss_config["class"])
    criterion = loss_class(**loss_config.get("params", {}))
    return criterion.to(device)


def train_graph_classifier(
    dataset: str,
    model_config: Dict,
    data_config: Dict,
    metrics_config: Dict,
    batch_size: int,
    nr_epochs: int,
    num_workers: int,
    optimizer: Dict,
    loss: Dict,
    config_path: str,
    experiment_name: str,
    test: bool,
    validation_frequency: int,
    clip_gradient_norm: Optional[float] = None,
    experiment_tags: Optional[Dict[str, Any]] = None,
    seed: Optional[int] = None,
) -> None:
    """Train the classification model for a given number of epochs.

    Args:
        model_config (Dict): Configuration of the models (gnn and classifier)
        data_config (Dict): Configuration of the data (e.g. splits)
        batch_size (int): Batch size
        nr_epochs (int): Number of epochs to train
        optimizer (Dict): Configuration of the optimizer
    """

    if test:
        data_config["overfit_test"] = True

    # Reproducibility
    seed = fix_seeds(seed)
    mlflow.set_experiment(experiment_name)
    if experiment_tags is not None:
        mlflow.set_tags(experiment_tags)
    mlflow.log_artifact(config_path, "config")
    log_sources()
    log_parameters(
        data=data_config,
        model=model_config,
        batch_size=batch_size,
        epochs=nr_epochs,
        optimizer=optimizer,
        loss=loss,
        seed=seed,
        clip_gradient_norm=clip_gradient_norm,
    )
    BACKGROUND_CLASS = dynamic_import_from(dataset, "BACKGROUND_CLASS")
    NR_CLASSES = dynamic_import_from(dataset, "NR_CLASSES")
    prepare_datasets = dynamic_import_from(dataset, "prepare_datasets")

    # Data loaders
    training_dataset, validation_dataset = prepare_datasets(**data_config)
    training_loader = DataLoader(
        training_dataset,
        batch_size=batch_size,
        shuffle=True,
        collate_fn=collate,
        num_workers=num_workers,
    )
    validation_loader = DataLoader(
        validation_dataset,
        batch_size=batch_size,
        collate_fn=collate,
        num_workers=num_workers,
    )

    # Compute device
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    if torch.cuda.is_available():
        mlflow.log_param("device", torch.cuda.get_device_name(0))
    else:
        mlflow.log_param("device", "CPU")

    # Model
    model = WeakTissueClassifier(**model_config)
    use_graph_head = model_config["graph_classifier_config"] is not None
    use_node_head = model_config["node_classifier_config"] is not None
    model = model.to(device)
    nr_trainable_total_params = sum(
        p.numel() for p in model.parameters() if p.requires_grad
    )
    mlflow.log_param("nr_parameters", nr_trainable_total_params)

    # Loss function
    if use_graph_head:
        graph_criterion = get_loss(loss, "graph", device)
    if use_node_head:
        node_criterion = get_loss(loss, "node", device)
    if use_graph_head and use_node_head:
        node_loss_weight = loss.get("node_weight", 0.5)
        assert (
            0.0 <= node_loss_weight <= 1.0
        ), f"Node weight loss must be between 0 and 1, but is {node_loss_weight}"
        graph_loss_weight = 1.0 - node_loss_weight

    training_metric_logger = GraphClassificationLoggingHelper(
        metrics_config,
        "train",
        background_label=BACKGROUND_CLASS,
        nr_classes=NR_CLASSES,
        node_loss_weight=node_loss_weight if use_graph_head and use_node_head else None,
        graph_loss_weight=graph_loss_weight
        if use_graph_head and use_node_head
        else None,
    )
    validation_metric_logger = GraphClassificationLoggingHelper(
        metrics_config,
        "valid",
        background_label=BACKGROUND_CLASS,
        nr_classes=NR_CLASSES,
        node_loss_weight=node_loss_weight if use_graph_head and use_node_head else None,
        graph_loss_weight=graph_loss_weight
        if use_graph_head and use_node_head
        else None,
    )

    # Optimizer
    optimizer_class = dynamic_import_from("torch.optim", optimizer["class"])
    optimizer = optimizer_class(model.parameters(), **optimizer["params"])

    for epoch in trange(nr_epochs):

        # Train model
        time_before_training = datetime.datetime.now()
        model.train()
        for iteration, (graph, graph_labels, node_labels) in enumerate(training_loader):
            graph = graph.to(device)
            graph_labels = graph_labels.to(device)
            node_labels = node_labels.to(device)

            graph_logits, node_logits = model(graph)

            if use_graph_head:
                graph_loss = graph_criterion(graph_logits, graph_labels)
            if use_node_head:
                node_loss = node_criterion(
                    node_logits, node_labels, graph.batch_num_nodes
                )
            if use_graph_head and use_node_head:
                combined_loss = (
                    graph_loss_weight * graph_loss + node_loss_weight * node_loss
                )
                combined_loss.backward()
            elif use_node_head:
                node_loss.backward()
            elif use_graph_head:
                graph_loss.backward()
            if clip_gradient_norm is not None:
                torch.nn.utils.clip_grad.clip_grad_norm_(
                    model.parameters(), clip_gradient_norm
                )
            optimizer.step()

            training_metric_logger.add_iteration_outputs(
                graph_loss=graph_loss.item() if use_graph_head else None,
                node_loss=node_loss.item() if use_node_head else None,
                graph_logits=graph_logits.detach().cpu()
                if graph_logits is not None
                else None,
                node_logits=node_logits.detach().cpu()
                if node_logits is not None
                else None,
                graph_labels=graph_labels.cpu(),
                node_labels=node_labels.cpu(),
                node_associations=graph.batch_num_nodes,
            )
        training_metric_logger.log_and_clear(step=epoch)
        training_epoch_duration = (
            datetime.datetime.now() - time_before_training
        ).total_seconds()
        mlflow.log_metric(
            "train.seconds_per_epoch", training_epoch_duration, step=epoch
        )

        if epoch % validation_frequency == 0:
            # Validate model
            time_before_validation = datetime.datetime.now()
            model.eval()
            with torch.no_grad():
                for iteration, (graph, graph_labels, node_labels) in enumerate(
                    validation_loader
                ):
                    graph = graph.to(device)
                    graph_labels = graph_labels.to(device)
                    node_labels = node_labels.to(device)

                    graph_logits, node_logits = model(graph)
                    if use_graph_head:
                        graph_loss = graph_criterion(graph_logits, graph_labels)
                    if use_node_head:
                        node_loss = node_criterion(
                            node_logits, node_labels, graph.batch_num_nodes
                        )

                    validation_metric_logger.add_iteration_outputs(
                        graph_loss=graph_loss.item() if use_graph_head else None,
                        node_loss=node_loss.item() if use_node_head else None,
                        graph_logits=graph_logits.detach().cpu()
                        if graph_logits is not None
                        else None,
                        node_logits=node_logits.detach().cpu()
                        if node_logits is not None
                        else None,
                        graph_labels=graph_labels.cpu(),
                        node_labels=node_labels.cpu(),
                        node_associations=graph.batch_num_nodes,
                    )
            validation_metric_logger.log_and_clear(
                step=epoch, model=model if not test else None
            )
            validation_epoch_duration = (
                datetime.datetime.now() - time_before_validation
            ).total_seconds()
            mlflow.log_metric(
                "valid.seconds_per_epoch", validation_epoch_duration, step=epoch
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=str, default="default.yml")
    parser.add_argument("--level", type=str, default="WARNING")
    parser.add_argument("--test", action="store_const", const=True, default=False)
    args = parser.parse_args()

    start_logging(args.level)
    assert Path(args.config).exists(), f"Config path does not exist: {args.config}"
    with open(args.config) as config_file:
        config = yaml.load(config_file, Loader=yaml.FullLoader)
    assert "train" in config, f"Config does not have an entry train ({config.keys()})"
    config = config["train"]

    logging.info("Start training")
    assert (
        "model" in config
    ), f"model not defined in config {args.config}: {config.keys()}"
    assert (
        "data" in config
    ), f"data not defined in config {args.config}: {config.keys()}"
    assert (
        "metrics" in config
    ), f"metrics not defined in config {args.config}: {config.keys()}"
    assert (
        "params" in config
    ), f"params not defined in config {args.config}: {config.keys()}"
    train_graph_classifier(
        model_config=config["model"],
        data_config=config["data"],
        metrics_config=config["metrics"],
        config_path=args.config,
        test=args.test,
        **config["params"],
    )
