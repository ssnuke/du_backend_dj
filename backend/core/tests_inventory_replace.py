import shutil
import tempfile

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from core.models import InventoryFile


class ReplaceInventoryUploadTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)

    def test_replacing_the_upload_updates_the_link_and_size(self):
        with override_settings(MEDIA_ROOT=self.media,
                               STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}):
            item = InventoryFile(title="Doc")
            item.admin_upload.save("old.txt", ContentFile(b"a" * 10), save=False)
            item.save()
            old_url = item.file_url
            self.assertIn("old", old_url)
            self.assertEqual(item.file_size, 10)

            item = InventoryFile.objects.get(pk=item.pk)
            item.admin_upload = ContentFile(b"b" * 25, name="new.txt")
            item.save()
            item.refresh_from_db()
            self.assertIn("new", item.file_url)
            self.assertNotEqual(item.file_url, old_url)
            self.assertEqual(item.file_size, 25)

    def test_saving_without_a_new_file_keeps_the_link(self):
        with override_settings(MEDIA_ROOT=self.media,
                               STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}):
            item = InventoryFile(title="Doc")
            item.admin_upload.save("keep.txt", ContentFile(b"x" * 5), save=False)
            item.save()
            url = item.file_url
            item = InventoryFile.objects.get(pk=item.pk)
            item.title = "Renamed"
            item.save()
            item.refresh_from_db()
            self.assertEqual(item.file_url, url)
