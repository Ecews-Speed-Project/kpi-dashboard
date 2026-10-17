import serve_dashboard as sd


def test_only_allowed_addresses_get_in():
    assert sd.allowed("jane.doe@ecews.org")
    assert sd.allowed("Someone@ECEWS.org ")
    assert not sd.allowed("someone@gmail.com")
    assert not sd.allowed("someone@ecews.org.evil.com")
    assert not sd.allowed("not-an-email")


def test_first_name_from_address():
    assert sd.first_name("jane.doe@ecews.org") == "Jane"
    assert sd.first_name("amaka_obi@ecews.org") == "Amaka"
    assert sd.first_name("jdoe2@ecews.org") == "Jdoe"


def test_session_cookie_is_signed():
    token = sd.sign("jane.doe@ecews.org")
    assert sd.verify(token) == "jane.doe@ecews.org"
    assert sd.verify(token[:-2] + "00") is None             # tampered signature
    assert sd.verify("garbage") is None
