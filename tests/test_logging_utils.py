from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from utils.logging_utils import PipelineLogger


class PipelineLoggerTests(unittest.TestCase):
    def test_save_writes_timestamped_fallback_when_processing_log_is_locked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = PipelineLogger(Path(tmpdir))
            logger.log("test", "*", "", "", "success", "ok")
            original_to_excel = type(logger.as_dataframe()).to_excel

            def write_or_lock(df, path, *args, **kwargs):
                if Path(path).name == "processing_log.xlsx":
                    raise PermissionError("locked")
                return original_to_excel(df, path, *args, **kwargs)

            with mock.patch("pandas.DataFrame.to_excel", new=write_or_lock):
                logger.save()

            fallback_logs = list(Path(tmpdir).glob("processing_log_*.xlsx"))
            self.assertEqual(len(fallback_logs), 1)
            self.assertTrue((Path(tmpdir) / "error_log.xlsx").exists())


if __name__ == "__main__":
    unittest.main()
