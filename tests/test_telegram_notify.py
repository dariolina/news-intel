import unittest
from unittest.mock import MagicMock, patch

from telegram_notify import chunk_text, send_plain_text


class ChunkTextTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(chunk_text(""), [])

    def test_single_chunk(self):
        self.assertEqual(chunk_text("abc"), ["abc"])

    def test_splits(self):
        self.assertEqual(chunk_text("abcd", max_len=2), ["ab", "cd"])


class SendPlainTextTests(unittest.TestCase):
    @patch("telegram_notify.requests.Session")
    def test_sends_chunks(self, session_cls):
        mock_sess = MagicMock()
        session_cls.return_value = mock_sess
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"ok": True}
        mock_sess.post.return_value = resp

        text = "x" * 5000
        send_plain_text("token", "-100", text, chunk_size=4000)

        self.assertEqual(mock_sess.post.call_count, 2)
        first = mock_sess.post.call_args_list[0][1]["json"]["text"]
        second = mock_sess.post.call_args_list[1][1]["json"]["text"]
        self.assertEqual(len(first), 4000)
        self.assertEqual(len(second), 1000)

    @patch("telegram_notify.requests.Session")
    def test_skips_blank(self, session_cls):
        mock_sess = MagicMock()
        session_cls.return_value = mock_sess
        send_plain_text("token", "-100", "   \n")
        mock_sess.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
