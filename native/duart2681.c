/* SCN2681 DUART -- see duart2681.h. Port of emu/duart2681.py. */
#include "duart2681.h"

#define SR_RXRDY 0x01
#define SR_TXRDY 0x04
#define SR_TXEMT 0x08

#define ISR_TXRDYA        0x01
#define ISR_RXRDYA        0x02
#define ISR_COUNTER_READY 0x08
#define ISR_TXRDYB        0x10
#define ISR_RXRDYB        0x20

#define CRYSTAL_HZ 3686400.0 /* DESIGN.md section 1 */

static void chan_init(duart_channel_t *ch)
{
    ch->mr1 = ch->mr2 = ch->mr_ptr = ch->csr = ch->cr = 0;
    ch->tx_enabled = ch->rx_enabled = 0;
    ch->rx_head = ch->rx_tail = ch->rx_count = 0;
}

static void chan_write_mr(duart_channel_t *ch, uint8_t value)
{
    if (ch->mr_ptr == 0) { ch->mr1 = value; ch->mr_ptr = 1; }
    else                 { ch->mr2 = value; }
}

static void chan_write_cr(duart_channel_t *ch, uint8_t value)
{
    ch->cr = value;
    /* bits 0-1: rx enable/disable, bits 2-3: tx enable/disable */
    if (value & 0x01) ch->rx_enabled = 1;
    if (value & 0x02) ch->rx_enabled = 0;
    if (value & 0x04) ch->tx_enabled = 1;
    if (value & 0x08) ch->tx_enabled = 0;
    if (((value >> 4) & 0x07) == 0x03) ch->mr_ptr = 0; /* reset MR pointer */
}

static uint8_t chan_status(const duart_channel_t *ch)
{
    uint8_t sr = 0;
    if (ch->rx_enabled && ch->rx_count > 0) sr |= SR_RXRDY;
    if (ch->tx_enabled) sr |= (SR_TXRDY | SR_TXEMT);
    return sr;
}

static int chan_pop(duart_channel_t *ch, uint8_t *out)
{
    if (ch->rx_count <= 0) return 0;
    *out = ch->rx[ch->rx_tail];
    ch->rx_tail = (ch->rx_tail + 1) % DUART_RXFIFO_SIZE;
    ch->rx_count--;
    return 1;
}

static uint8_t duart_isr(const scn2681_t *d)
{
    uint8_t isr = 0;
    if (d->a.tx_enabled) isr |= ISR_TXRDYA;
    if (d->a.rx_enabled && d->a.rx_count > 0) isr |= ISR_RXRDYA;
    if (d->counter_ready) isr |= ISR_COUNTER_READY;
    if (d->b.tx_enabled) isr |= ISR_TXRDYB;
    if (d->b.rx_enabled && d->b.rx_count > 0) isr |= ISR_RXRDYB;
    return isr;
}

static void update_irq(scn2681_t *d)
{
    int active = (duart_isr(d) & d->imr) ? 1 : 0;
    if (active != d->irq_state) {
        d->irq_state = active;
        d->irq_cb(d->owner, active);
    }
}

/* ---- counter/timer ---------------------------------------------------
 * ACR[6:4] select mode/clock per the datasheet. Only the crystal-derived
 * sources are modeled (this firmware programs ACR=0xFF -> Timer mode,
 * X1/CLK/16); the IP2- and TxC-derived sources aren't wired to anything in
 * our emulated environment, so those modes simply never tick.
 */
static int counter_is_timer_mode(const scn2681_t *d) { return (d->acr >> 6) & 1; }

static int32_t counter_n(const scn2681_t *d)
{
    int32_t n = ((int32_t)d->ctur << 8) | d->ctlr;
    return n ? n : 0x10000; /* 0 means max count (65536) per datasheet */
}

static int32_t counter_period_ticks(const scn2681_t *d)
{
    /* Counter mode: fires once after N clocks (one-shot). Timer mode: the
     * chip free-runs a square wave toggling every N clocks, so the ISR
     * "ready" event happens once per full period, i.e. every 2N. */
    int32_t n = counter_n(d);
    return counter_is_timer_mode(d) ? (2 * n) : n;
}

static double compute_counter_clock_hz(const scn2681_t *d)
{
    unsigned sel = (d->acr >> 4) & 0x7;
    if (sel == 0x3 || sel == 0x7) return CRYSTAL_HZ / 16.0; /* Counter/Timer, X1/16 */
    if (sel == 0x6)               return CRYSTAL_HZ;        /* Timer, X1 x1 */
    return 0.0;
}

static void start_counter(scn2681_t *d)
{
    d->counter_remaining = counter_period_ticks(d);
    d->counter_tick_debt = 0.0;
    d->counter_running = 1;
}

static void stop_counter(scn2681_t *d)
{
    /* Per the SCN2681 command set, Stop Counter/Timer halts counting only
     * in Counter mode (one-shot; needs a fresh Start to re-arm). In Timer
     * mode it only disables the OP3 output and acks/clears the pending
     * interrupt -- the free-running countdown is untouched, so periodic
     * ISR-ready events keep coming with no further Start command. This
     * firmware's ISR bit-3 handler reads this register unconditionally on
     * every tick as its interrupt-ack, with no re-arm anywhere in the ROM,
     * which only makes sense under Timer-mode semantics. Confirmed
     * empirically: with an unconditional halt, the tick counter fired
     * exactly once and never again (DESIGN.md section 11). */
    d->counter_ready = 0;
    if (!counter_is_timer_mode(d)) d->counter_running = 0;
    update_irq(d);
}

void duart_step(scn2681_t *d, double dt_seconds)
{
    double hz;
    int32_t ticks;
    int fired = 0;

    if (!d->counter_running) return;
    hz = d->counter_clock_cache;
    if (hz <= 0.0) return;

    d->counter_tick_debt += dt_seconds * hz;
    ticks = (int32_t)d->counter_tick_debt;
    if (ticks <= 0) return;
    d->counter_tick_debt -= (double)ticks;
    d->counter_remaining -= ticks;

    while (d->counter_remaining <= 0) {
        fired = 1;
        if (counter_is_timer_mode(d)) {
            d->counter_remaining += counter_period_ticks(d);
        } else {
            d->counter_running = 0;
            d->counter_remaining = 0;
            break;
        }
    }
    if (fired) {
        d->counter_ready = 1;
        update_irq(d);
    }
}

/* ---- lifecycle ------------------------------------------------------- */
void duart_init(scn2681_t *d, void *owner, duart_tx_fn on_tx_b, duart_irq_fn irq_cb)
{
    d->owner = owner;
    d->on_tx_b = on_tx_b;
    d->irq_cb = irq_cb;
    d->irq_state = 0;
    duart_reset(d);
}

void duart_reset(scn2681_t *d)
{
    chan_init(&d->a);
    chan_init(&d->b);
    d->imr = 0;
    d->acr = 0;
    d->opr = 0;
    d->irq_state = 0;
    /* IP0=CTS, IP2=DSR, IP3=RLS default low; IP4 default low = "skip self
     * test" ACTIVE (matches the real dipswitch default documented in the
     * MAME driver); IP5/IP6 are undocumented jumpers left at their
     * Open/VCC (high) default; IP7 doesn't exist as a real pin, reads high. */
    d->input_port_bits = 0xE0;
    d->input_port_read_count = 0;
    d->ctur = d->ctlr = 0;
    d->counter_running = 0;
    d->counter_remaining = 0;
    d->counter_tick_debt = 0.0;
    d->counter_ready = 0;
    d->counter_clock_cache = 0.0;
}

void duart_set_input_bit(scn2681_t *d, int bit, int level)
{
    /* bit: 0=CTS, 2=DSR, 3=RLS. The firmware runs a modem-style connect
     * state machine on its host port; our virtual link has no real modem,
     * so the machine glue asserts CTS/DSR permanently (DESIGN.md s1). */
    if (level) d->input_port_bits |= (uint8_t)(1u << bit);
    else       d->input_port_bits &= (uint8_t)~(1u << bit);
}

int duart_feed_rx_b(scn2681_t *d, const uint8_t *data, int len)
{
    int i;
    for (i = 0; i < len; i++) {
        if (d->b.rx_count >= DUART_RXFIFO_SIZE) break;
        d->b.rx[d->b.rx_head] = data[i];
        d->b.rx_head = (d->b.rx_head + 1) % DUART_RXFIFO_SIZE;
        d->b.rx_count++;
    }
    update_irq(d);
    return i;
}

int duart_rx_b_pending(const scn2681_t *d) { return d->b.rx_count; }

/* ---- register access -------------------------------------------------- */
uint8_t duart_read(scn2681_t *d, int offset)
{
    int reg = offset & 0x1E;
    uint8_t v = 0;

    switch (reg) {
    case 0x00: return d->a.mr_ptr == 0 ? d->a.mr1 : d->a.mr2;
    case 0x02: return chan_status(&d->a);
    case 0x06: (void)chan_pop(&d->a, &v); return v;
    case 0x08: return 0; /* IPCR: no pending input changes modeled */
    case 0x0A: return duart_isr(d);
    case 0x10: return d->b.mr_ptr == 0 ? d->b.mr1 : d->b.mr2;
    case 0x12: return chan_status(&d->b);
    case 0x16:
        if (chan_pop(&d->b, &v)) update_irq(d);
        return v;
    case 0x1A: {
        /* MAME's own documented workaround ("hack to prevent hang when skip
         * self test is shorted"): IP4 must read differently on the second+
         * read of this port than on the first, or the self-test dispatcher
         * hangs. Bit4 = IP4 = skip-self-test (low = active). */
        uint8_t value;
        d->input_port_read_count++;
        value = d->input_port_bits;
        if (d->input_port_read_count > 1) value |= 0x10;
        return value;
    }
    case 0x1C: /* read = Start Counter/Timer command */
        start_counter(d);
        return (uint8_t)((d->counter_remaining >> 8) & 0xFF);
    case 0x1E: /* read = Stop Counter/Timer command */
        stop_counter(d);
        return (uint8_t)(d->counter_remaining & 0xFF);
    default: return 0;
    }
}

void duart_write(scn2681_t *d, int offset, uint8_t value)
{
    int reg = offset & 0x1E;

    switch (reg) {
    case 0x00: chan_write_mr(&d->a, value); break;
    case 0x02: d->a.csr = value; break;
    case 0x04: chan_write_cr(&d->a, value); break;
    case 0x06: break; /* THRA: channel A not used as host link, discard */
    case 0x08:
        d->acr = value;
        d->counter_clock_cache = compute_counter_clock_hz(d);
        break;
    case 0x0A: d->imr = value; update_irq(d); break;
    case 0x0C: d->ctur = value; break;
    case 0x0E: d->ctlr = value; break;
    case 0x10: chan_write_mr(&d->b, value); break;
    case 0x12: d->b.csr = value; break;
    case 0x14: chan_write_cr(&d->b, value); break;
    case 0x16: d->on_tx_b(d->owner, value); break;
    case 0x1A: break; /* OPCR: output port config, not modeled */
    case 0x1C: d->opr |= value; break;  /* set output port bits */
    case 0x1E: d->opr &= (uint8_t)~value; break; /* reset output port bits */
    default: break;
    }
}
