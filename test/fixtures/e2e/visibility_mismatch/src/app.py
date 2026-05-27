class Service:
    def process(self, x: int) -> int:
        return self._internal_helper(x) * 2

    def _internal_helper(self, x: int) -> int:
        return x + 1
