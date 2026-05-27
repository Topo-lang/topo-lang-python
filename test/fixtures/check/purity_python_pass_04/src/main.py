# Both functions write to module globals — would be a purity violation
# under [purity].mode = "force", but mode = "off" emits a Note and exits 0.

impurity = 0


def compute():
    global impurity
    impurity = impurity + 1


def render():
    global impurity
    impurity = impurity * 2
