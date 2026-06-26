export type EventEnvelope<TPayload> = {
  eventType: string;
  version: number;
  payload: TPayload;
};
