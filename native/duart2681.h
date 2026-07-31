/* SCN2681 DUART -- C port of emu/duart2681.py. Only what the DTC-01 v2.0
 * firmware actually uses: channel B as the host RS-232 link, channel A
 * stubbed, the interrupt/mask registers, the input-port handshake lines,
 * and the counter/timer (which drives the firmware's entire system tick --
 * see DESIGN.md sections 10-11).
 *
 * Register offsets are word-stepped: the DTC-01 wires the DUART's A0-A3
 * select lines to the 68000's A1-A4, so each register sits 2 bytes apart
 * (DESIGN.md section 3, "a0 not connected").
 */
#ifndef DTC01_DUART2681_H
#define DTC01_DUART2681_H

#include <stdint.h>

#define DUART_RXFIFO_SIZE 512  /* host->DECtalk staging buffer */

typedef void (*duart_tx_fn)(void *owner, uint8_t byte);
typedef void (*duart_irq_fn)(void *owner, int active);

typedef struct {
    uint8_t mr1, mr2, mr_ptr, csr, cr;
    int tx_enabled, rx_enabled;
    uint8_t rx[DUART_RXFIFO_SIZE];
    int rx_head, rx_tail, rx_count;
} duart_channel_t;

typedef struct {
    duart_channel_t a, b;
    uint8_t imr, acr, opr;
    uint8_t ctur, ctlr;

    int      counter_running;
    int32_t  counter_remaining;   /* clock ticks to next ready/reload boundary */
    double   counter_tick_debt;   /* fractional clock cycles between steps */
    int      counter_ready;       /* latched ISR bit 3 */
    double   counter_clock_cache; /* cached clock rate; refreshed on ACR write */

    uint8_t input_port_bits;
    int     input_port_read_count;

    void         *owner;
    duart_tx_fn   on_tx_b;
    duart_irq_fn  irq_cb;
    int           irq_state;
} scn2681_t;

void    duart_init(scn2681_t *d, void *owner, duart_tx_fn on_tx_b, duart_irq_fn irq_cb);
void    duart_reset(scn2681_t *d);
void    duart_set_input_bit(scn2681_t *d, int bit, int level);
int     duart_feed_rx_b(scn2681_t *d, const uint8_t *data, int len); /* returns bytes accepted */
int     duart_rx_b_pending(const scn2681_t *d);
uint8_t duart_read(scn2681_t *d, int offset);
void    duart_write(scn2681_t *d, int offset, uint8_t value);
void    duart_step(scn2681_t *d, double dt_seconds);

#endif /* DTC01_DUART2681_H */
