# Learning Log: Phase 0 (Environment)

My own explanations after the phase, with corrections. These are my notes for the demo video.

**1. Why do `uv.lock` and a pinned `requirements.txt` make the project reproducible?**
Another user can install the same packages using `uv.lock` and `requirements.txt`.
> Correction: *exactly* the same versions. `pyproject.toml` only gives ranges ("fastapi >= 0.163");
> `uv.lock` pins the exact version and hash of every package, including sub-dependencies.
> `requirements.txt` is the same pinned list for pip users.

**2. Why do the corpus PDFs stay out of Git?**
Some PDFs hold data that should not be shared publicly.
> Addition: the FBR DI spec says it should not be redistributed, and large binaries bloat the repo
> forever. Others get identical files by downloading from the `source_url` in `corpus/manifest.jsonl`
> and checking the `sha256` recorded there.

**3. If `.env` were committed once and then added to `.gitignore`, would it be safe?**
We should not commit the `.env` file.
> Correction: no, it would not be safe. `.gitignore` only affects untracked files; a committed file
> stays in Git history, and anyone can read it on a public repo. The fix is to rotate every key, then
> remove the file from history. That is why `.gitignore` had to exist before the first commit.
