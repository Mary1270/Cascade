"""
Minimal offline stub of the `genlayer` SDK.

THIS IS A TEST-ONLY SHIM, NOT PART OF THE DEPLOYABLE CONTRACTS.

The real GenLayer SDK (`genlayer` package + GenVM runtime) is only
available inside a GenLayer node / GenLayer Studio / testnet, and
provides trustless, consensus-checked implementations of web access,
LLM calls, the caller's cryptographic identity, cross-contract calls, and
the equivalence-principle voting protocols.

This stub is adapted from the pattern used in the sibling MatchGuard
project's `tests/genlayer_stub/`, with one addition MatchGuard never
needed: MatchGuard is a single contract, so its stub never implements
`gl.get_contract_at(...)`. A genuine multi-contract system whose entire
mechanism depends on real cross-contract `.view()`/`.emit()` calls needs
it -- and this is exactly what genlayer-test's own "Direct Mode" was
confirmed, live in CI, NOT to actually execute (see LESSONS_LEARNED.md
from the Vigil project, where this was first discovered). This stub adds
a small in-process contract registry so `get_contract_at(address).view()`
/`.emit()` route to the real target contract instance, synchronously,
letting the whole system's real cross-contract logic run offline in
plain Python.

For fast, fully offline unit testing of this project's DETERMINISTIC
logic (tier/threshold selection, deltas, authorization checks, wiring),
this stub reproduces just enough of the SDK's surface area to import and
exercise the contracts directly, with `gl.nondet.web.render` /
`gl.nondet.exec_prompt` monkeypatched per test case and
`gl.message.sender_address` settable per test case to simulate different
callers.

It intentionally does NOT attempt to simulate:
  - real network access,
  - real LLM behavior,
  - multi-validator consensus / leader-validator divergence,
  - `.emit()`'s real asynchronous, separate-transaction timing (this
    stub calls the target method immediately/synchronously, which is
    fine for asserting the eventual state these tests check), or
  - GenVM's real deterministic clock.

Those require the actual GenLayer Studio or testnet -- see the
project's README and LESSONS_LEARNED.md for how the live end-to-end
tests were run there.
"""

__all__ = [
    "gl", "TreeMap", "u256", "DynArray", "i256", "bigint", "Address",
    "register_contract", "clear_registry",
]


class _SubscriptableContainer:
    """Base for storage-type stand-ins that support `Type[K, V]` syntax
    used in class-level annotations (e.g. `TreeMap[Address, u256]`)."""

    def __class_getitem__(cls, item):
        return cls


class TreeMap(_SubscriptableContainer, dict):
    """Stand-in for genlayer's persistent TreeMap - behaves like a dict."""


class DynArray(_SubscriptableContainer, list):
    """Stand-in for genlayer's persistent DynArray - behaves like a list."""


class u256(int):
    """Stand-in for genlayer's fixed-width unsigned integer type."""


class i256(int):
    """Stand-in for genlayer's fixed-width signed integer type."""


class bigint(int):
    """Stand-in for genlayer's arbitrary-precision integer type."""


class Address:
    """
    Minimal stand-in for genlayer's Address type: accepts either a
    40-hex-character "0x"-prefixed string or a raw 20-byte big-endian
    bytes object (Vigil's contracts construct their ZERO_ADDRESS
    constant this way), and compares/hashes case-insensitively,
    matching how EVM-style addresses behave.
    """

    def __init__(self, value):
        if isinstance(value, bytes):
            if len(value) != 20:
                raise ValueError(f"invalid address bytes length: {len(value)}")
            text = "0x" + value.hex()
        else:
            text = str(value)
        if not text.startswith("0x") or len(text) != 42:
            raise ValueError(f"invalid address: {text!r}")
        int(text[2:], 16)  # raises ValueError if not valid hex
        self._value = text

    def __str__(self):
        return self._value

    def __repr__(self):
        return f"Address({self._value!r})"

    def __eq__(self, other):
        return str(self).lower() == str(other).lower()

    def __hash__(self):
        return hash(str(self).lower())


class UserError(Exception):
    """Stand-in for genlayer.gl.vm.UserError."""


class _Vm:
    """Stand-in for `gl.vm` - exposes UserError at its real SDK path."""

    UserError = UserError


class _WriteDecorator:
    """Stand-in for `gl.public.write`, which in the real SDK is also used
    as `@gl.public.write.payable` for methods that accept a GEN deposit
    (this stub doesn't simulate balance transfer/holding - a payable
    method's behavior only depends on `gl.message.value`, which tests set
    directly via `set_value()`, so no separate wrapping is needed)."""

    def __call__(self, fn):
        return _wrap_public_method(fn)

    @staticmethod
    def payable(fn):
        return _wrap_public_method(fn)


class _PublicNamespace:
    """Stand-in for `gl.public`. Wraps each method so that, for the
    duration of its execution, the CURRENTLY EXECUTING contract's own
    registered address is tracked on `_CALLER_STACK` -- this is what
    lets a nested `get_contract_at(...).view()/.emit()` call (see
    below) correctly simulate real GenVM's rule that
    `gl.message.sender_address`, inside the CALLED contract, is the
    calling CONTRACT's address, not the original human sender."""

    write = _WriteDecorator()

    @staticmethod
    def view(fn):
        return _wrap_public_method(fn)


def _wrap_public_method(fn):
    def wrapper(self, *args, **kwargs):
        my_address = _INSTANCE_TO_ADDRESS.get(id(self))
        _CALLER_STACK.append(my_address)
        try:
            return fn(self, *args, **kwargs)
        finally:
            _CALLER_STACK.pop()
    return wrapper


class _NondetWeb:
    """
    Stand-in for `gl.nondet.web`. `render` raises by default; tests
    monkeypatch this with `unittest.mock.patch.object` to simulate
    specific fetch outcomes.
    """

    @staticmethod
    def render(url, mode="text"):
        raise NotImplementedError(
            "gl.nondet.web.render must be patched in tests"
        )


class _Nondet:
    web = _NondetWeb()

    @staticmethod
    def exec_prompt(prompt, response_format="text"):
        raise NotImplementedError(
            "gl.nondet.exec_prompt must be patched in tests"
        )


class _EqPrinciple:
    """
    Stand-in for `gl.eq_principle`. For offline unit tests we simply run
    `fn` once and return its result; simulating real multi-validator
    consensus requires the live GenLayer Studio/testnet (see
    LESSONS_LEARNED.md for that live verification).
    """

    @staticmethod
    def strict_eq(fn):
        return fn()

    @staticmethod
    def prompt_comparative(fn, principle=None):
        return fn()

    @staticmethod
    def prompt_non_comparative(fn, task="", criteria=""):
        return fn()


class _Message:
    """
    Stand-in for `gl.message`. `sender_address` and `value` are plain
    mutable attributes here (in the real SDK they're derived from the
    actual signed transaction) - tests set them directly before each
    call to simulate a specific caller/value, e.g.:

        gl.message.sender_address = Address("0x" + "11" * 20)
    """

    sender_address = None
    value = 0


class _Contract:
    """
    Stand-in base class for `gl.Contract`.

    In real GenVM, fields declared with persistent storage types
    (TreeMap, DynArray, ...) are automatically backed by chain state
    and start out empty - contracts are not expected to initialize
    them by hand in `__init__`. This stub reproduces that by scanning
    class annotations at construction time and pre-populating any
    TreeMap/DynArray fields with empty instances before the contract's
    own `__init__` runs.
    """

    def __new__(cls, *args, **kwargs):
        instance = super().__new__(cls)
        for klass in reversed(cls.__mro__):
            for name, annotation in vars(klass).get("__annotations__", {}).items():
                if isinstance(annotation, type) and issubclass(
                    annotation, (TreeMap, DynArray)
                ):
                    setattr(instance, name, annotation())
        return instance

    def __init__(self, *args, **kwargs):
        pass


# --- cross-contract call support (not needed by MatchGuard, needed here) ---

_CONTRACT_REGISTRY = {}
_INSTANCE_TO_ADDRESS = {}
_CALLER_STACK = []


def _normalize_key(address) -> str:
    return str(Address(address)).lower()


def register_contract(address, instance) -> None:
    """Test-side bookkeeping: record which contract instance a given
    address resolves to (for `get_contract_at` lookups), and the
    reverse mapping (for `_CALLER_STACK`, see `_PublicNamespace`
    above)."""
    _CONTRACT_REGISTRY[_normalize_key(address)] = instance
    _INSTANCE_TO_ADDRESS[id(instance)] = str(Address(address))


def clear_registry() -> None:
    _CONTRACT_REGISTRY.clear()
    _INSTANCE_TO_ADDRESS.clear()
    _CALLER_STACK.clear()


class _CrossContractProxy:
    """What `get_contract_at(...).view()` / `.emit()` actually return.
    Wraps every method call on the target so that, for its duration,
    `gl.message.sender_address` is temporarily the CALLING contract's
    own address (matching real GenVM), then restores whatever it was
    before once the call returns."""

    def __init__(self, target, caller_address):
        self._target = target
        self._caller_address = caller_address

    def __getattr__(self, name):
        attr = getattr(self._target, name)
        if not callable(attr):
            return attr

        def wrapper(*args, **kwargs):
            previous_sender = gl.message.sender_address
            if self._caller_address is not None:
                gl.message.sender_address = Address(self._caller_address)
            try:
                return attr(*args, **kwargs)
            finally:
                gl.message.sender_address = previous_sender

        return wrapper


class _ContractHandle:
    """Stand-in for the object returned by `gl.get_contract_at(...)`."""

    def __init__(self, address):
        self._address = address

    def _target(self):
        key = _normalize_key(self._address)
        if key not in _CONTRACT_REGISTRY:
            raise RuntimeError(
                f"no contract registered at {self._address} -- did the "
                f"test call register_contract() for it?"
            )
        return _CONTRACT_REGISTRY[key]

    def _caller_address(self):
        return _CALLER_STACK[-1] if _CALLER_STACK else None

    def view(self):
        return _CrossContractProxy(self._target(), self._caller_address())

    def emit(self, value=0):
        return _CrossContractProxy(self._target(), self._caller_address())

    def emit_transfer(self, value=0):
        pass  # no GEN-transfer simulation needed for Vigil's tests


def get_contract_at(address):
    return _ContractHandle(address)


class _GL:
    Contract = _Contract
    public = _PublicNamespace()
    nondet = _Nondet()
    eq_principle = _EqPrinciple()
    vm = _Vm()
    message = _Message()
    get_contract_at = staticmethod(get_contract_at)


gl = _GL()
