import argparse
import time

from kafka import KafkaProducer


def main() -> None:
    parser = argparse.ArgumentParser(description="Send test messages to Aiven Kafka over SSL.")
    parser.add_argument("--bootstrap-server", required=True, help="host:port")
    parser.add_argument("--topic", default="trp", help="Kafka topic name")
    parser.add_argument("--ca-file", default="ca.pem", help="Path to CA cert")
    parser.add_argument("--cert-file", default="service.cert", help="Path to client cert")
    parser.add_argument("--key-file", default="service.key", help="Path to client key")
    parser.add_argument("--count", type=int, default=100, help="Number of messages to send")
    parser.add_argument("--interval-seconds", type=float, default=1.0, help="Sleep interval between sends")
    args = parser.parse_args()

    producer = KafkaProducer(
        bootstrap_servers=args.bootstrap_server,
        security_protocol="SSL",
        ssl_cafile=args.ca_file,
        ssl_certfile=args.cert_file,
        ssl_keyfile=args.key_file,
    )

    for i in range(args.count):
        message = f"Hello from Python using SSL {i + 1}!"
        producer.send(args.topic, message.encode("utf-8"))
        print(f"Message sent: {message}")
        time.sleep(args.interval_seconds)

    producer.flush()
    producer.close()


if __name__ == "__main__":
    main()
