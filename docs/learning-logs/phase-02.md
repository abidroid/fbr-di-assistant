# Learning Log: Phase 2 (Auth)

Concepts: hashing vs encryption, argon2, JWT (signed, not encrypted), bearer tokens and the
`CurrentUser` dependency, 404 instead of 403 for other users' data, a separate test database.

**1. Why argon2 instead of storing passwords?**

My words: Passwords should never be stored as plain text.

Fuller answer (from review): Hashing is one-way. The server can check a typed password against
the stored hash, but it can never turn the hash back into the password, so a leaked `users` table
does not leak passwords. argon2 is slow and memory-heavy on purpose, so an attacker who steals the
hashes cannot try millions of guesses cheaply. A random salt is stored inside each hash, so two
users with the same password get different hashes.

**2. What does the server check when a token arrives?**

My words: The bearer token is a secret, so only authorized users can access data.

Fuller answer (from review): The token itself is **not** secret: it is only base64, so anyone can
decode it and read the user id and expiry. What makes it safe is the **signature**: only the
server has `JWT_SECRET_KEY`, so nobody can change or forge a token. On every protected request:
1. `OAuth2PasswordBearer` reads `Authorization: Bearer <token>`; no header means 401.
2. `decode_access_token` (`app/security.py`) checks the HS256 signature with our secret key,
   that `exp` has not passed, that the algorithm is the one we pinned, and that `sub` exists.
3. `get_user_from_token` (`app/services/auth_service.py`) checks `sub` is a valid UUID and that the
   user still exists in the database.
4. The route receives the `User` object through `CurrentUser`.

Any failure returns 401 with `WWW-Authenticate: Bearer`, in our standard error shape.

**3. Why do tests run on a separate Neon branch?**

My words: So that production data is not changed.

Fuller answer (from review): Correct. Also, the test fixtures run `TRUNCATE ... CASCADE` on every
table before each test, which would wipe the development data. Each test also needs a known, empty
starting point so its result is the same every time it runs.
