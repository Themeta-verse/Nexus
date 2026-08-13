from nexus.security import hash_password, hash_session_token, verify_password


def test_password_hash_is_salted_and_verifiable() -> None:
    password = "CorrectHorse9!"
    first = hash_password(password)
    second = hash_password(password)
    assert first != second
    assert verify_password(password, first)
    assert not verify_password("WrongHorse9!", first)


def test_session_token_hash_requires_the_pepper() -> None:
    token = "opaque-session-token"
    first = hash_session_token(token, "pepper-a")
    second = hash_session_token(token, "pepper-b")
    assert first != second
    assert first == hash_session_token(token, "pepper-a")
