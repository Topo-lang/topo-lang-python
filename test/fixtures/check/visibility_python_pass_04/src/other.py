# Cross-namespace private call would be a violation, but
# [visibility].mode = "off" should suppress diagnostics.


def invoke():
    # `helper` lives in `app.py` (private). With mode=off the call is
    # not reported. Reference via attribute form for the extractor's
    # dot-to-:: canonicalization.
    import app
    app.helper()
