from uuid import UUID

from app.domain.calls import CallConfiguration

INTERNAL_CALL_ID = UUID("12345678-1234-5678-1234-567812345678")


def call_configuration(**overrides: object) -> CallConfiguration:
    data: dict[str, object] = {
        "internal_call_id": INTERNAL_CALL_ID,
        "destination_name": "Loja Exemplo",
        "destination_number": "+351211234567",
        "objective": "Confirmar a disponibilidade e o preço de Echeverias.",
        "context": "O proprietário procura plantas para hoje.",
        "preferences": "Preferir variedades pequenas.",
        "constraints": "Não reservar nem comprar.",
        "language": "pt-PT",
        "authorized_actions": [],
    }
    data.update(overrides)
    return CallConfiguration.model_validate(data)


def call_request(**overrides: object) -> dict[str, object]:
    data = call_configuration(**overrides).model_dump(
        mode="json", exclude={"internal_call_id"}
    )
    return data
