"""Recording semantics; hardware feedback codes are deployment-owned data."""
LEGACY_GAIT_CODES = {0x1001: 'flat', 0x3002: 'flat', 0x1003: 'stairs', 0x3003: 'stairs'}


def code_number(value):
    if isinstance(value, str):
        try:
            value = int(value, 16 if value.lower().startswith('0x') else 10)
        except ValueError:
            raise ValueError('Gait code must be an integer or decimal/hex integer string') from None
    if type(value) is not int or not 0 <= value <= 0xffffffff:
        raise ValueError('Gait code must be a uint32 integer')
    return value


def profile(mode='legged', codes=None):
    if mode not in ('legged', 'wheeled'):
        raise ValueError('locomotion_mode must be legged or wheeled')
    if mode == 'wheeled':
        return dict(locomotion_mode=mode, gait_code_map={})
    codes = LEGACY_GAIT_CODES if codes is None else codes
    if not isinstance(codes, dict) or not codes:
        raise ValueError('gait.code_map must be a nonempty mapping')
    normalized = {}
    for raw, label in codes.items():
        code = str(code_number(raw))
        if code in normalized or label not in ('flat', 'stairs', 'unknown'):
            raise ValueError('Duplicate gait code or invalid label; use flat/stairs/unknown')
        normalized[code] = label
    return dict(locomotion_mode=mode, gait_code_map=normalized)


def stored_profile(value):
    # Original maps predate wheel support and used the legacy legged code table.
    return profile(value.get('locomotion_mode', 'legged'), value.get('gait_code_map'))


def require_compatible(a, b, operation):
    if stored_profile(a) != stored_profile(b):
        raise ValueError(f'Incompatible locomotion mode or gait code mapping for {operation}; '
                         'use a matching deployment or create a new map; automatic conversion is not supported')


def gait_label(codes, code):
    try:
        return codes.get(str(code_number(code)), 'unknown')
    except ValueError:
        return 'unknown'


def feedback_value(raw, field):
    for part in field.split('.'):
        raw = raw[part]
    return code_number(raw)
