/**
 * Typed access to the same JSON the Python seed script reads
 * (e2e/seed/e2e-data.json), so a spec can assert on "Ada Lovelace's Custom
 * Tote" instead of a magic string, and both sides of the fixture can never
 * drift into two different datasets.
 */
import rawData from "../seed/e2e-data.json";

export interface PaymentFixture {
  amount: number;
  paidOffsetDays: number;
  method?: string;
}

export interface OrderFixture {
  key: string;
  item: string;
  status: "tentative" | "confirmed" | "ready" | "delivered" | "cancelled";
  startOffsetDays: number;
  dueOffsetDays: number;
  isRush: boolean;
  price: number;
  payments: PaymentFixture[];
}

export interface ClientFixture {
  key: string;
  firstName: string;
  lastName: string;
  email: string;
  phone: string;
  orders: OrderFixture[];
}

export interface E2EData {
  company: { name: string };
  adminUser: { email: string; password: string; fullName: string };
  freshUser: { email: string; password: string; fullName: string };
  clients: ClientFixture[];
}

export const testData = rawData as E2EData;

export function clientByKey(key: string): ClientFixture {
  const client = testData.clients.find((c) => c.key === key);
  if (!client) throw new Error(`No fixture client with key "${key}"`);
  return client;
}

export function orderByKey(key: string): { client: ClientFixture; order: OrderFixture } {
  for (const client of testData.clients) {
    const order = client.orders.find((o) => o.key === key);
    if (order) return { client, order };
  }
  throw new Error(`No fixture order with key "${key}"`);
}

export function clientFullName(client: ClientFixture): string {
  return `${client.firstName} ${client.lastName}`;
}

/** Mirrors the Python seed script's `date.today() + timedelta(days=offset)`. */
export function isoDateOffset(offsetDays: number, from: Date = new Date()): string {
  const d = new Date(from);
  d.setDate(d.getDate() + offsetDays);
  return d.toISOString().slice(0, 10); // YYYY-MM-DD, matching order.start.strftime('%Y-%m-%d')
}

export function orderTotal(order: OrderFixture): number {
  return order.price;
}

export function orderAmountPaid(order: OrderFixture): number {
  return order.payments.reduce((sum, p) => sum + p.amount, 0);
}

export function orderBalanceDue(order: OrderFixture): number {
  return orderTotal(order) - orderAmountPaid(order);
}

export function clientLifetimeValue(client: ClientFixture): number {
  // Mirrors CL2a: cancelled orders never count toward lifetime value.
  return client.orders
    .filter((o) => o.status !== "cancelled")
    .reduce((sum, o) => sum + orderTotal(o), 0);
}

export function formatMoney(amount: number): string {
  return `$${amount.toFixed(2)}`;
}
