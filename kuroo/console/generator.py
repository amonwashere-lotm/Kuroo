import string, secrets

from kuroo.memory.cache import cache

def generate_redeem_code(length:int=20):
    code = ''.join((secrets.choice(string.ascii_uppercase + string.digits) for _ in range(length//2))) + '-' + ''.join((secrets.choice(string.ascii_uppercase + string.digits) for _ in range(length//2)))
    if cache.redeem_codes.get(code):
        return generate_redeem_code(length)
    return code

