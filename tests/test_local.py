import socket
import unittest

from price_action_bot.local import find_free_port


class LocalLauncherTests(unittest.TestCase):
    def test_skips_a_port_that_is_already_in_use(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            used_port = listener.getsockname()[1]
            listener.listen()
            self.assertEqual(find_free_port(used_port, used_port + 2), used_port + 1)


if __name__ == "__main__":
    unittest.main()
