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


def test_first_sign_in_is_kept_and_only_si_gets_live(tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "USERS_FILE", tmp_path / "users.json")
    assert sd.users() == {} and not sd.can_live("ada@ecews.org")
    sd.register("ada@ecews.org", "Ada", "SI")
    sd.register("ben@ecews.org", "Ben", "Lab")
    assert sd.users()["ada@ecews.org"]["first_name"] == "Ada"
    assert sd.can_live("ada@ecews.org") and not sd.can_live("ben@ecews.org")
    monkeypatch.setattr(sd, "LIVE_EMAILS", {"someone@ecews.org"})
    assert not sd.can_live("ada@ecews.org")


def test_session_ends_after_the_limit(monkeypatch):
    token = sd.sign("jane.doe@ecews.org")
    now = sd.time.time()
    monkeypatch.setattr(sd.time, "time", lambda: now + sd.SESSION_SECONDS + 1)
    assert sd.verify(token) is None


def test_partner_domain_gets_in_once_configured(monkeypatch):
    assert not sd.allowed("x@fhi360.org")
    monkeypatch.setattr(sd, "PARTNER_NAMES", {"fhi": "FHI", "ahni": "AHNI"})
    assert sd.allowed("x@fhi360.org") and sd.partner_of("x@ahnigeria.org") == "AHNI"
    assert not sd.allowed("fhi@gmail.com")             # the name must be in the domain, not before the @
