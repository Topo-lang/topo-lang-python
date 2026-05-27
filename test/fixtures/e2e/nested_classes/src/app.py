class Outer:
    def run(self):
        pass

    class Inner:
        def compute(self, x: int) -> int:
            return x * 2

        class Deep:
            def name(self) -> str:
                return "deep"
