### Deploy From a Clean, Committed Tree → skill `deploy-ssh`

A deploy copies bytes to a live target: `git status --porcelain` MUST be empty first — the `pre-deploy-clean-tree.sh` hook blocks rsync/scp/sftp/sshpass pushes from a dirty tree (fail-closed). Full protocol (deploy from a committed ref, post-deploy diff-verify vs HEAD, record deployed SHA, the hook-unwatched vectors: docker build of the workdir, `tar c | ssh`, sftp batch) moved VERBATIM into the `deploy-ssh` skill — load it before ANY manual deploy.
