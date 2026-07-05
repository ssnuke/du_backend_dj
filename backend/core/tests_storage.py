import io

import boto3
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from moto import mock_aws
from PIL import Image

from core.storage import R2Storage

_TEST_BUCKET = "du-test-bucket"


def _make_image_bytes(fmt="JPEG", size=(800, 600), color=(255, 0, 0)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format=fmt)
    buf.seek(0)
    return buf.read()


@override_settings(
    AWS_ACCESS_KEY_ID="testing",
    AWS_SECRET_ACCESS_KEY="testing",
    AWS_STORAGE_BUCKET_NAME=_TEST_BUCKET,
    AWS_S3_REGION_NAME="us-east-1",
    AWS_S3_ENDPOINT_URL=None,
    AWS_S3_CUSTOM_DOMAIN=None,
    AWS_DEFAULT_ACL=None,
    AWS_QUERYSTRING_AUTH=False,
)
class R2StorageTests(TestCase):
    """
    Verifies R2Storage's thumbnail-generation behavior against a moto-mocked
    S3 bucket (R2's API is S3-compatible, so moto's mock is a valid stand-in
    for the real thing here). Real R2 credentials are never needed for this.
    """

    def setUp(self):
        self.mock = mock_aws()
        self.mock.start()
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=_TEST_BUCKET)
        self.storage = R2Storage()

    def tearDown(self):
        self.mock.stop()

    def test_image_upload_generates_thumbnail(self):
        saved_name = self.storage._save("chat/rooms/1/photo.jpg", ContentFile(_make_image_bytes()))
        self.assertTrue(self.storage.exists(saved_name))
        self.assertTrue(self.storage.exists(R2Storage._thumb_name(saved_name)))

    def test_video_upload_generates_no_thumbnail(self):
        saved_name = self.storage._save("chat/rooms/1/clip.mp4", ContentFile(b"not a real video, just bytes"))
        self.assertTrue(self.storage.exists(saved_name))
        self.assertFalse(self.storage.exists(R2Storage._thumb_name(saved_name)))

    def test_thumbnail_is_smaller_than_original(self):
        saved_name = self.storage._save("chat/rooms/1/big.jpg", ContentFile(_make_image_bytes(size=(2000, 1500))))
        original_size = self.storage.size(saved_name)
        thumb_size = self.storage.size(R2Storage._thumb_name(saved_name))
        self.assertLess(thumb_size, original_size)

    def test_corrupt_image_does_not_fail_upload(self):
        # _save_thumbnail swallows failures so a bad/corrupt image never
        # blocks the actual upload — only the thumbnail is skipped.
        saved_name = self.storage._save("chat/rooms/1/corrupt.png", ContentFile(b"not actually a png"))
        self.assertTrue(self.storage.exists(saved_name))
        self.assertFalse(self.storage.exists(R2Storage._thumb_name(saved_name)))
