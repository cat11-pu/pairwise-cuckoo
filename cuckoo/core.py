"""Cuckoo hash table kernel.

Layout notes
------------
* Two slot arrays of the same capacity hold the entries.  Every key owns one
  slot in the first array and one slot in the second, and home(key) reports
  both of them, so a lookup only has to inspect those two slots.
* put() seats a key in its first home slot.  When that slot is taken the
  occupant is kicked out and moved to its own other home slot, which may kick
  another key in turn; the displacement chain is capped by max_kicks.
* A chain that runs past max_kicks means the two arrays are too crowded for the
  current key set: every stored pair is moved into a pair of arrays twice as
  big, the second home function is reshuffled and the seating starts over.  A
  table that even at its largest attempt cannot seat every pair (a home
  function that maps everything onto one slot, say) raises TableFullError
  instead of dropping a key.
* The two arrays grow whenever a new key would push them past half full, so
  len(table) * 2 never climbs above capacity.
* delete() clears the slot its key owns and leaves every other slot alone.
  Empty slots are the only thing a lookup may stop at.
* The two home functions are built in and deterministic: they never fall back
  to the builtin hash(), so one key set always produces the same layout,
  whatever PYTHONHASHSEED says.  A caller that wants a layout of its own can
  pass hashes=(first, second); each callable is handed a key and returns an
  integer, which the kernel reduces modulo the current capacity.

Only the Python standard library is used and the module does no I/O.
"""

EMPTY = None

MIN_CAPACITY = 8

MAX_KICKS = 16

GROWTH_FACTOR = 2

MAX_ATTEMPTS = 6

_MASK64 = 0xFFFFFFFFFFFFFFFF

_SALT_STEP = 0x9E3779B97F4A7C15

_SEED = 0x243F6A8885A308D3


class TableFullError(RuntimeError):
    """Raised when even the largest pair of arrays cannot seat every key."""


def _mix64(value):
    """SplitMix64 finalizer: spread the bits of an unsigned 64 bit value."""
    value = (value ^ (value >> 33)) * 0xFF51AFD7ED558CCD & _MASK64
    value = (value ^ (value >> 33)) * 0xC4CEB9FE1A85EC53 & _MASK64
    return value ^ (value >> 33)


def stable_hash(key):
    """Return an unsigned 64 bit hash for an int or a str key."""
    if isinstance(key, str):
        value = 0xCBF29CE484222325
        for char in key:
            value ^= ord(char)
            value = (value * 0x100000001B3) & _MASK64
        return _mix64(value ^ len(key))
    return _mix64(key & _MASK64)


class CuckooTable(object):
    """Two-array cuckoo table over int and str keys."""

    def __init__(self, capacity=MIN_CAPACITY, max_kicks=MAX_KICKS, hashes=None):
        if isinstance(capacity, bool) or not isinstance(capacity, int):
            raise TypeError("capacity must be an int")
        if capacity < 2:
            raise ValueError("capacity must be at least 2")
        if isinstance(max_kicks, bool) or not isinstance(max_kicks, int):
            raise TypeError("max_kicks must be an int")
        if max_kicks < 1:
            raise ValueError("max_kicks must be positive")
        if hashes is not None:
            hashes = tuple(hashes)
            if len(hashes) != 2 or not all(callable(item) for item in hashes):
                raise TypeError("hashes must be a pair of callables")
        self._capacity = capacity
        self._max_kicks = max_kicks
        self._hashes = hashes
        self._seed = _SEED
        self._tables = [[EMPTY] * capacity, [EMPTY] * capacity]
        self._count = 0
        self._kicks = 0
        self._rebuilds = 0

    # ------------------------------------------------------------------
    # introspection
    # ------------------------------------------------------------------
    @property
    def capacity(self):
        """Number of slots in each of the two arrays."""
        return self._capacity

    @property
    def max_kicks(self):
        """Displacements one seating may make before the arrays grow."""
        return self._max_kicks

    @property
    def kicks(self):
        """Displacements the table has performed so far."""
        return self._kicks

    @property
    def rebuilds(self):
        """Times the table has been moved into a fresh pair of arrays."""
        return self._rebuilds

    @property
    def tables(self):
        """Snapshot of both arrays: EMPTY or a (key, value) pair."""
        return [[EMPTY if entry is None else (entry[0], entry[1]) for entry in table]
                for table in self._tables]

    def __len__(self):
        return self._count

    def __contains__(self, key):
        self._check_key(key)
        return self._find(key) is not None

    # ------------------------------------------------------------------
    # home slots
    # ------------------------------------------------------------------
    @staticmethod
    def _check_key(key):
        if isinstance(key, bool) or not isinstance(key, (int, str)):
            raise TypeError("key must be an int or a str")

    def home(self, key):
        """The two slot indices of key: first array, then second array."""
        self._check_key(key)
        return self._home(key, 1), self._home(key, 2)

    def _home(self, key, which):
        """Slot index of key in array `which` (1 or 2)."""
        if self._hashes is not None:
            return self._hashes[which - 1](key) % self._capacity
        digest = stable_hash(key)
        if which == 2:
            digest = _mix64(digest ^ self._seed)
        return digest % self._capacity

    # ------------------------------------------------------------------
    # lookups
    # ------------------------------------------------------------------
    def _find(self, key):
        """Return (which, index) for a stored key, or None when it is absent."""
        for which in (1, 2):
            index = self._home(key, which)
            entry = self._tables[which - 1][index]
            if entry is not None and entry[0] == key:
                return which, index
        return None

    def get(self, key, default=None):
        """Return the value stored under key, or default when it is absent."""
        self._check_key(key)
        found = self._find(key)
        if found is None:
            return default
        which, index = found
        return self._tables[which - 1][index][1]

    def _entries(self):
        """Yield every stored pair, first array first, in slot order."""
        for table in self._tables:
            for entry in table:
                if entry is not None:
                    yield entry

    # ------------------------------------------------------------------
    # seating
    # ------------------------------------------------------------------
    def _place(self, key, value):
        """Seat one key, kicking occupants aside.

        Returns None once the key is seated, or the pair that is left without a
        slot when the displacement chain ran past max_kicks."""
        where = 1
        for _ in range(self._max_kicks):
            index = self._home(key, where)
            table = self._tables[where - 1]
            occupant = table[index]
            table[index] = (key, value)
            if occupant is None:
                return None
            key, value = occupant
            self._kicks += 1
            where = 3 - where
        return key, value

    def _reset(self, capacity):
        """Replace both arrays with empty ones and reshuffle the second hash."""
        self._capacity = capacity
        self._seed = _mix64(self._seed + _SALT_STEP)
        self._tables = [[EMPTY] * capacity, [EMPTY] * capacity]
        self._rebuilds += 1

    def _rebuild(self, entries):
        """Seat every pair in `entries` into the current (empty) arrays,
        doubling the arrays whenever the pairs do not settle."""
        keys = list(entries)
        for _ in range(MAX_ATTEMPTS):
            pending = list(keys)
            settled = True
            while pending:
                carry = self._place(*pending.pop())
                if carry is not None:
                    settled = False
                    break
            if settled:
                return
            self._reset(self._capacity * GROWTH_FACTOR)
        raise TableFullError("no pair of arrays up to %d slots seats every key" % self._capacity)

    def _needs_growth(self):
        """True when one more key would climb above one pair per two slots."""
        return (self._count + 1) * 2 > self._capacity

    def _grow(self):
        """Double the arrays and seat the stored keys again."""
        entries = list(self._entries())
        self._reset(self._capacity * GROWTH_FACTOR)
        self._rebuild(entries)

    def put(self, key, value):
        """Store value under key, kicking and growing whenever it has to."""
        self._check_key(key)
        found = self._find(key)
        if found is not None:
            which, index = found
            self._tables[which - 1][index] = (key, value)
            return
        if self._needs_growth():
            self._grow()
        carry = self._place(key, value)
        if carry is not None:
            entries = list(self._entries()) + [carry]
            self._reset(self._capacity * GROWTH_FACTOR)
            self._rebuild(entries)
        self._count += 1

    def delete(self, key):
        """Remove key and report whether it was stored."""
        self._check_key(key)
        found = self._find(key)
        if found is None:
            return False
        which, index = found
        self._tables[which - 1][index] = EMPTY
        self._count -= 1
        return True

    # ------------------------------------------------------------------
    # iteration
    # ------------------------------------------------------------------
    def items(self):
        """Every stored (key, value) pair, first array first, in slot order."""
        return self._entries()

    def keys(self):
        return [key for key, _ in self.items()]

    def values(self):
        return [value for _, value in self.items()]

    def __iter__(self):
        return iter(self.items())
