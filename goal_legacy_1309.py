"""Recognition of review-role `/goal` lines armed before #1309.

#1309 (owner 12.11.2026): the review (B) gained the window's own count and the
"work your own I tickets" directive. Review lines armed before that stay
recognised, so the goal janitor still clears its own leftover.
"""

_PRE_1309_REVIEW = (
    ("montalu/slovnormal/miva == main A `core-quals --role review --count` == 0"
     " — inak", "montalu/slovnormal/miva == main — inak"),
    ("iba zakladajú; vlastné I tickety (`core-quals --role review --list`) "
     "pracuj v každom cykle — čakanie na waitery nie je práca (#1309); ",
     "iba zakladajú; "),
)


def pre_1309(line, role):
    """The pre-#1309 form of a rendered goal line (unchanged unless review)."""
    if role != "review":
        return line
    for new, old in _PRE_1309_REVIEW:
        line = line.replace(new, old, 1)
    return line
