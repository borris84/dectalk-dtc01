/* TMS32010 DSP core -- C port of emu/tms32010.py, which is itself a
 * direct instruction-for-instruction port of MAME's BSD-3-Clause
 * "tms320c1x" device (src/devices/cpu/tms320c1x/tms320c1x.cpp, Tony La
 * Porta). See DESIGN.md section 1 and 4.
 *
 * Every MAME-derived quirk is preserved verbatim (TBLR/TBLW's
 * STACK[0]=STACK[1] shuffle, SST never updating ARP, interrupts that can
 * be set but never externally cleared, taken-branch cycle doubling). The
 * real DSP firmware was written and tested against this exact behavior.
 */
#ifndef DTC01_TMS32010_H
#define DTC01_TMS32010_H

#include <stdint.h>

#define TMS_ADDR_MASK    0xFFF   /* 12-bit program space (TMS320C10) */
#define TMS_PROGRAM_SIZE 0x1000
#define TMS_DATA_SIZE    0x100

typedef uint16_t (*tms_io_read_fn)(void *owner, int port);
typedef void     (*tms_io_write_fn)(void *owner, int port, uint16_t value);
typedef int      (*tms_bio_read_fn)(void *owner);

typedef struct tms32010 {
    uint16_t program[TMS_PROGRAM_SIZE];
    uint16_t data[TMS_DATA_SIZE];

    uint16_t PC;
    uint16_t PREVPC;
    uint16_t STR;      /* status register */
    uint32_t ACC;
    uint32_t ALU;
    uint32_t Preg;
    uint16_t Treg;
    uint16_t AR[2];
    uint16_t STACK[4];
    uint16_t opcode;
    uint32_t oldacc;
    uint8_t  memaccess;

    int int_pending;
    int in_reset;
    int branch_taken;

    void            *owner;
    tms_io_read_fn   io_read;
    tms_io_write_fn  io_write;
    tms_bio_read_fn  bio_read;
} tms32010_t;

void tms_init(tms32010_t *c, const uint16_t *program_words, int nwords,
              void *owner, tms_io_read_fn io_read, tms_io_write_fn io_write,
              tms_bio_read_fn bio_read);
void tms_reset(tms32010_t *c);
void tms_set_reset_line(tms32010_t *c, int asserted);
void tms_set_int_line(tms32010_t *c, int asserted);

/* Execute one instruction; returns cycles consumed. */
int tms_step(tms32010_t *c);

/* Run at least `cycles` worth of instructions. Returns the (<=0) overshoot
 * so the caller can carry the remainder into the next quantum, mirroring
 * MAME's do-while(icount>0) scheduling. */
int tms_run(tms32010_t *c, int cycles);

#endif /* DTC01_TMS32010_H */
