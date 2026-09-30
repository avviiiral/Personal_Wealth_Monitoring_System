import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.core.management.base import BaseCommand


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class Command(BaseCommand):
    help = "Generate a VAPID key pair for browser Web Push."

    def handle(self, *args, **options):
        private_key = ec.generate_private_key(ec.SECP256R1())
        private_der = private_key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_key = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        )

        self.stdout.write("")
        self.stdout.write("Add these values to backend/.env (never commit the private key):")
        self.stdout.write("")
        self.stdout.write(
            f"WEB_PUSH_VAPID_PUBLIC_KEY={_b64url(public_key)}"
        )
        self.stdout.write(
            f"WEB_PUSH_VAPID_PRIVATE_KEY={_b64url(private_der)}"
        )
        self.stdout.write(
            "WEB_PUSH_VAPID_SUBJECT=mailto:admin@example.com"
        )
        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "VAPID key pair generated. Keep WEB_PUSH_VAPID_PRIVATE_KEY secret."
            )
        )
