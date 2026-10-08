from dataclasses import dataclass, field


@dataclass
class Reply:
    text: str
    buttons: list[list[tuple[str, str]]] = field(default_factory=list)  # rows of (label, callback_data)
    edit: bool = True  # for button taps: edit the card in place; False = send a new message
