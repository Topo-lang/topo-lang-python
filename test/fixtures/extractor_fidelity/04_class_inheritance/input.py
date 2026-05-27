class Base:
    def setup(self):
        pass


class Child(Base):
    def setup(self):
        super().setup()
