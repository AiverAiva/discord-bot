# from datetime import datetime, timezone

# def aware_utcnow():
#     return datetime.now(timezone.utc)

# def aware_utcfromtimestamp(timestamp):
#     return datetime.fromtimestamp(timestamp, timezone.utc)

# def naive_utcnow():
#     return aware_utcnow().replace(tzinfo=None)

# def naive_utcfromtimestamp(timestamp):
#     return aware_utcfromtimestamp(timestamp).replace(tzinfo=None)

# print(aware_utcnow())
# print(aware_utcfromtimestamp(0))
# print(naive_utcnow())
# print(naive_utcfromtimestamp(0))
# print(datetime.utcnow())
def calculate_xp_needed(base_xp, exponent, level):
    return base_xp * (level ** exponent)

base_xp = 100
exponent = 1.3
levels = 120

xp_needed = [calculate_xp_needed(base_xp, exponent, level) for level in range(1, levels + 1)]
for level, xp in enumerate(xp_needed, 1):
    print(f"Level {level}: {xp:.2f} XP")