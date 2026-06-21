import os
import tempfile
import unittest

from looma import cli


class CliDefaultTest(unittest.TestCase):
    def test_today_subparser_routes_to_cmd_today(self):
        args = cli.build_parser().parse_args(["today"])
        self.assertIs(args.func, cli.cmd_today)

    def test_bare_looma_runs_today(self):
        # `looma` with no args should run the daily driver, not error.
        with tempfile.TemporaryDirectory() as t:
            db = os.path.join(t, "looma.db")
            old = os.environ.get("LOOMA_DB")
            os.environ["LOOMA_DB"] = db
            try:
                # init first so the store exists, then bare invocation
                self.assertEqual(cli.main(["init"]), 0)
                rc = cli.main([])  # bare -> today
                self.assertEqual(rc, 0)
            finally:
                if old is None:
                    os.environ.pop("LOOMA_DB", None)
                else:
                    os.environ["LOOMA_DB"] = old


if __name__ == "__main__":
    unittest.main()
