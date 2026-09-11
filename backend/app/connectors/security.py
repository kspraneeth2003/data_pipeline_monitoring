from app.crypto import encrypt_secret, is_encrypted_secret

SECRET_FIELDS = {
    "SNOWFLAKE": ["password", "privateKey"],
    "POSTGRES": ["password"],
}


def encrypt_config_secrets(connector_type: str, config: dict) -> dict:
    result = dict(config)
    for field in SECRET_FIELDS.get(connector_type, []):
        value = result.get(field)
        if isinstance(value, str) and value and not is_encrypted_secret(value):
            result[field] = encrypt_secret(value)
    return result


def redact_config_secrets(connector_type: str, config: dict) -> dict:
    result = dict(config)
    for field in SECRET_FIELDS.get(connector_type, []):
        had_value = bool(result.get(field))
        result.pop(field, None)
        flag_key = f"has{field[0].upper()}{field[1:]}"
        result[flag_key] = had_value
    return result
