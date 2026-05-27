class Service:
    def run_a(self): return 1
    def run_b(self): return 2


def dispatch(name_id):
    target = Service()
    method_name = "run_" + str(name_id)
    func = getattr(target, method_name)
    return func()
