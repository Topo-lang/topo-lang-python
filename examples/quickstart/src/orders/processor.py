class Invoice:
    def __init__(self, total: float = 0.0, tracking_id: str = ""):
        self.total = total
        self.tracking_id = tracking_id


def process_order(order_id: int) -> Invoice:
    validate_order(order_id)
    payment = charge_payment(order_id, 99.99)
    shipping = calculate_shipping(order_id)
    invoice = create_invoice(order_id, payment, shipping)
    send_confirmation(invoice)
    update_analytics(invoice)
    return invoice


def validate_order(order_id: int) -> bool:
    check_inventory(order_id)
    verify_address(order_id)
    return True


def charge_payment(order_id: int, amount: float) -> float:
    amount = apply_discount(order_id, amount)
    return amount


def calculate_shipping(order_id: int) -> float:
    return 5.99


def create_invoice(order_id: int, payment: float, shipping: float) -> Invoice:
    return Invoice(total=payment + shipping, tracking_id=f"ORD-{order_id}")


def check_inventory(order_id: int) -> bool:
    return True


def verify_address(order_id: int) -> bool:
    return True


def apply_discount(order_id: int, amount: float) -> float:
    return amount * 0.9


def send_confirmation(invoice: Invoice) -> None:
    pass


def update_analytics(invoice: Invoice) -> None:
    pass


def dump_order_state(order_id: int) -> None:
    pass
