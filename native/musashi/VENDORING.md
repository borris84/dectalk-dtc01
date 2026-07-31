# Vendored: Musashi 4.60 (Motorola 68000 emulation core)

Upstream: https://github.com/kstenerud/Musashi (branch `master`, version 4.60)
License: **MIT** (Copyright Karl Stenerud) — full text in `LICENSE.txt`,
also reproduced verbatim in the header of every source file.

## Why this core

- MIT-licensed, so it can ship inside the `.nvda-addon` package.
- It is the same lineage MAME's own `dec/dectalk.cpp` driver runs on, i.e.
  effectively the reference implementation for this exact machine.
- It correctly auto-vectors **internally-generated** CPU exceptions
  (address error, bus error, privilege violation). That is precisely the
  capability Unicorn's M68K backend lacks (unicorn-engine/unicorn#1502)
  which forced the hand-written Python core — see DESIGN.md §9. The
  DTC-01's mandatory boot self-test depends on it.

## Files fetched verbatim from upstream

```
m68k.h  m68kconf.h  m68kcpu.h  m68kcpu.c  m68kmake.c  m68k_in.c
m68kfpu.c  m68kmmu.h  softfloat/{milieu.h,softfloat.h,softfloat.c,
softfloat-macros,softfloat-specialize,mamesf.h}
```

`m68k_in.c` is byte-identical (SHA1 `f8edf509823a72bac2e0b990861cb0442ac7542e`)
to `research/musashi_m68k_in.c`, the opcode table our Python core was
validated against via `tools/verify_opcodes.py` — so the C and Python
cores are built from the same opcode ground truth.

`softfloat/` is required even for a 68000-only build: `m68kcpu.h`
includes it unconditionally in 4.60.

## Generated files (checked in)

`m68kops.h` / `m68kops.c` are **generated**, not upstream sources:

```sh
cl /nologo /Od /Fe:m68kmake.exe m68kmake.c     # /Od IS REQUIRED -- see below
./m68kmake.exe . m68k_in.c                     # -> 1967 handlers from 518 primitives
```

Re-run this if `m68k_in.c` is ever updated.

### ⚠ Build `m68kmake` with `/Od`, never `/O2`

MSVC 14.44 (x64) **miscompiles `m68kmake.c` at `/O2`**. The mask/match
generation loop

```c
op->op_mask  |= (bitpattern[i] != '.') << (15-i);
op->op_match |= (bitpattern[i] == '1') << (15-i);
```

silently loses bit 14 (`0x4000`) from *every* generated mask and match.
Verified on identical input, same source, same compiler — only the
optimisation level differs:

| build | pattern `1010............` | pattern `11110010........` |
|---|---|---|
| `/Od` | mask `f000` match `a000` ✅ | mask `ff00` match `f200` ✅ |
| `/O2` | mask `b000` match `a000` ❌ | mask `bf00` match `b200` ❌ |

(The same expression compiled standalone at `/O2` is correct, so this is
specific to the full translation unit — not a bug in the expression.)

**Why it's catastrophic rather than subtle:** Musashi's generated
`m68ki_build_opcode_table()` walks the handler table with
`while(ostruct->mask != 0xff00)`, expecting the `0xff00` mask group as a
terminator. With bit 14 stripped, no entry ever has mask `0xff00`, so the
scan runs past the end of the array and `m68k_init()` dies with an access
violation before a single instruction executes. Symptom: the DLL loads,
`dtc01_create()` crashes.

Sanity check after regenerating — all four groups must be non-empty:

```sh
grep -c ", 0xff00, " m68kops.c   # expect 18
grep -c ", 0xf1f8, " m68kops.c   # expect 533
grep -c ", 0xffff, " m68kops.c   # expect 471
grep -c ", 0xf000, " m68kops.c   # expect 4
```

Only `m68kmake` (the one-shot generator) needs `/Od`. The emulator itself
is built at `/O2 /GL` as usual — its correctness is verified against the
Python reference by `tools/compare_native.py`.

## Local modifications

`m68kconf.h` only (it is upstream's designated configuration header —
editing it is the intended integration workflow). Changes:

| Setting | Upstream | Ours | Why |
|---|---|---|---|
| `M68K_EMULATE_010/EC020/020/030/040` | ON | **OFF** | DTC-01 is a plain 68000; smaller/faster core |
| `M68K_EMULATE_PMMU` | ON | **OFF** | no MMU on this machine |
| `M68K_EMULATE_ADDRESS_ERROR` | **OFF** | **ON** | **critical** — the boot self-test deliberately triggers address errors (DESIGN.md §9) |

No other upstream file is modified.
