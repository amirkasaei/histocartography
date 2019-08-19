"""Whole Slide Image IO module."""
import os
import logging
import sys
import boto3

# setup logging
# logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
log = logging.getLogger('Histocartography::IO::UTILS')
h1 = logging.StreamHandler(sys.stdout)
log.setLevel(logging.DEBUG)
formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
h1.setFormatter(formatter)
log.addHandler(h1)


def get_s3(endpoint_url='http://data.digital-pathology.zc2.ibm.com:9000',
           aws_access_key_id=None,
           aws_secret_access_key=None):

    if aws_access_key_id is None or aws_secret_access_key is None:
        try:
            aws_access_key_id = os.environ["AWS_ACCESS_KEY_ID"]
            aws_secret_access_key = os.environ["AWS_SECRET_ACCESS_KEY"]
        except KeyError:
            log.error("No access keys provided for {}. Please set Environment Variables".format(
                endpoint_url))

    s3 = boto3.resource('s3',
                        endpoint_url=endpoint_url,
                        aws_access_key_id=aws_access_key_id,
                        aws_secret_access_key=aws_secret_access_key
                        )

    log.debug("S3 Connection established")

    return s3


def download_file_to_local(s3=None, bucket_name='test-data',
                           s3file='test_wsi.svs',
                           local_name='tmp.svs'):

    if s3 is None:
        s3 = get_s3()
    try:
        with open(local_name, "wb") as file:
            filepath = os.path.abspath(file.name)
            dirname = os.path.dirname(file.name)
            log.debug('Downloading %s to %s', local_name, filepath)
            log.debug('Currently contains: %s', os.listdir(dirname))
            s3.meta.client.download_fileobj(bucket_name, s3file, file)

    except Exception as error:
        log.error("%s could not be downloaded to %s", s3file, local_name)
        log.error(str(error))
        local_name = None

    return local_name


def save_local_file(local_file, s3=None, bucket_name=None, s3file=None):
    if s3 is None:
        s3 = get_s3()

    try:
        s3.meta.client.upload_file(
            Filename=local_file, Bucket=bucket_name, Key=s3file)
        log.debug("{} saved to {}/{} as {}".format(local_file,
                                                   s3, bucket_name, s3file))
    except Exception as e:
        log.error("Error saving {} to {}/{} as {}".format(local_file,
                                                          s3, bucket_name, s3file))
        log.error(str(e))