"""Read the values saved on the Settings page. Each app saves its own, under its own name."""


def _saved(env, key):
    value = env["ir.config_parameter"].sudo().get_param(key)
    return None if value in (None, False, "") else value


def param_int(env, key, default):
    try:
        value = _saved(env, key)
        return default if value is None else int(value)
    except (TypeError, ValueError):
        return default


def param_float(env, key, default):
    try:
        value = _saved(env, key)
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def param_text(env, key, default):
    value = _saved(env, key)
    return default if value is None else value
