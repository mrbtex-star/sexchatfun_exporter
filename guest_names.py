"""Human-like guest username generator for SexChatFun.

Avoids random alphanumeric strings that look like bots.
Uses realistic name patterns with optional numbers/edits.
"""

import random


# Base names — first + last combinations that sound human, casual, 18+ friendly
FIRST_NAMES = [
    "jake", "tyler", "mike", "chris", "alex", "derek", "ryan", "blake",
    "ashley", "jessica", "taylor", "amanda", "nicole", "brittany", "sarah",
    "kevin", "brandon", "dustin", "jason", "justin", "tyrone", "lamar",
    "keisha", "tamika", "tasha", "monique", "shayla", "tiffani", "laurie",
    "mark", "anthony", "dennis", "wayne", "earl", "gerald", "terrell",
    "victoria", "vanessa", "stephanie", "cassandra", "debbie", "karen",
    "rachel", "michelle", "cynthia", "angela", "sharon", "linda", "susan",
    "brenda", "pamela", "nancy", "karen", "betty", "helen", "dorothy",
]


LAST_NAMES = [
    "smith", "jones", "williams", "brown", "davis", "miller", "wilson",
    "moore", "taylor", "anderson", "thomas", "jackson", "white", "harris",
    "martin", "garcia", "martinez", "robinson", "clark", "rodriguez",
    "lewis", "lee", "walker", "hall", "allen", "young", "hernandez",
    "king", "wright", "lopez", "hill", "scott", "green", "adams",
    "baker", "gonzalez", "nelson", "carter", "mitchell", "perez",
    "roberts", "turner", "phillips", "campbell", "parker", "evans",
    "edwards", "collins", "stewart", "morris", "murphy", "rivera",
    "cook", "rogers", "morgan", "cooper", "peterson", "bailey",
]


def generate_username(style: str = "initial") -> str:
    """Generate a human-like username using the site's format:
    First initial + next 4 letters of last name + random single digit.
    E.g., JESKA3, MILEY9, DAVID7
    Only letters A-Z and digits 0-9 — no underscores.
    """
    first = random.choice(FIRST_NAMES)
    last = random.choice(LAST_NAMES)

    # First letter of first name (4 chars)
    prefix = first[:4].upper()
    # 5th letter (index 4)
    middle = last[4:5].upper() if len(last) > 4 else ''
    # If last name is shorter than 4, pad with last letter or just use what we have
    if len(last) <= 4:
        mid = last[-1:].upper() if last else ''
    else:
        mid = last[4].upper()
    
    # Combine: 4 from first + 1 from last
    core = prefix + mid
    
    # Pad to 5 letters if needed (rare edge case)
    while len(core) < 5:
        core += random.choice('AEIOU')
    core = core[:5]
    
    # Random single digit
    num = str(random.randint(0, 9))
    
    return core + num


def generate_batch(count: int = 20) -> list[str]:
    """Generate a batch of unique usernames."""
    seen = set()
    names = []
    for _ in range(count * 2):
        name = generate_username()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
            if len(names) >= count:
                break
    return names


if __name__ == "__main__":
    # Generate a demo list
    names = generate_batch(25)
    print(f"Generated {len(names)} human-like usernames:")
    for i, n in enumerate(names):
        print(f"  {i+1:2d}. {n}")
