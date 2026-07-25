### CI Push Discipline → on-demand skill `ci-push-discipline`

The full pre-push/post-push protocol moved VERBATIM to the `ci-push-discipline` skill — load it before and immediately after every `git push`. Non-negotiable that survives here: sync the base FIRST (`git fetch && git merge origin/<base>`), run local lint before pushing, and never assume a repo auto-cancels a superseded CI run — most repos have no concurrency group, so a re-push starts a SECOND parallel run unless you cancel the old one.
