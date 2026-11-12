"""Recognition of review-role `/goal` lines armed before #1309.

#1309 (owner 12.11.2026): the review (B) gained the window's own count and the
"work your own I tickets" directive. Review lines armed before that stay
recognised, so the goal janitor still clears its own leftover.
"""

_PRE_1309_REVIEW = (
    ("montalu/slovnormal/miva == main A `core-quals --role review --count` == 0"
     " — inak", "montalu/slovnormal/miva == main — inak"),
    ("iba zakladajú; vlastné I tickety (`core-quals --role review --list`) "
     "pracuj každý cyklus po (b)/(c), dep-wait odblokuj či založ; waiter nie "
     "je práca (#1309); ",
     "iba zakladajú; "),
)


def pre_1309(line, role):
    """The pre-#1309 form of a rendered goal line (unchanged unless review)."""
    if role != "review":
        return line
    for new, old in _PRE_1309_REVIEW + ((_V1_A[1], "iba zakladajú; "),):
        line = line.replace(new, old, 1)
    return line


# The first #1309 wording (0.1.639), before review round 2 added the (b)/(c)
# priority and the dep-wait rule. Lines armed from 0.1.639 stay recognised.
_V1_A = (_PRE_1309_REVIEW[1][0],
         "iba zakladajú; vlastné I tickety (`core-quals --role review --list`) "
         "pracuj v každom cykle — čakanie na waitery nie je práca (#1309); ")


def prior_1309_lines(line, role):
    """Every earlier deployed form of a review line: pre-#1309 and #1309 v1."""
    if role != "review":
        return []
    return [pre_1309(line, role), line.replace(_V1_A[0], _V1_A[1], 1)]
