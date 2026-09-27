import re

class MikroTikParser(object):
    def init(self, options):
        pattern = r".*src-mac\s+(?P<MACADDR>(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}),\s+proto\s+(?P<PROTO>[^,]+),\s+(?P<SRCIP>\d+\.\d+\.\d+\.\d+):(?P<SRCPORT>\d+)->(?P<DSTIP>\d+\.\d+\.\d+\.\d+):(?P<DSTPORT>\d+).*"
        self.regex = re.compile(pattern)
        return True

    def deinit(self):
        pass

    def parse(self, log_message):
        text = log_message['MESSAGE']
        match = self.regex.search(text)
        if match:
            for k, v in match.groupdict().items():
                log_message[k] = v
            return True
        return False
