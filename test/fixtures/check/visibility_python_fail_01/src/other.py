# `other.invoke` reaches into `app.helper`, which is declared private in
# namespace `app`. Cross-namespace private calls are forbidden.


def invoke():
    import app
    app.helper()  # violation: cross-namespace private call
