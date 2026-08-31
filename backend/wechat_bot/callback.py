"""Parsing for decrypted WeCom callback events."""

from __future__ import annotations

from dataclasses import dataclass
import xml.etree.ElementTree as ET


class CallbackParseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CustomerServiceEvent:
    event: str
    token: str
    open_kfid: str
    create_time: int


def parse_callback_event(
    xml: str, expected_corp_id: str
) -> CustomerServiceEvent | None:
    if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
        raise CallbackParseError("DTD and entities are not allowed")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise CallbackParseError("decrypted callback XML is invalid") from exc

    corp_id = root.findtext("ToUserName", "").strip()
    if corp_id != expected_corp_id:
        raise CallbackParseError("callback ToUserName does not match CorpID")
    event = root.findtext("Event", "").strip()
    if event != "kf_msg_or_event":
        return None
    token = root.findtext("Token", "").strip()
    open_kfid = root.findtext("OpenKfId", "").strip()
    if not token or not open_kfid:
        raise CallbackParseError("customer-service callback has no Token or OpenKfId")
    try:
        create_time = int(root.findtext("CreateTime", "0"))
    except ValueError as exc:
        raise CallbackParseError("callback CreateTime is invalid") from exc
    return CustomerServiceEvent(event, token, open_kfid, create_time)


def parse_customer_service_event(
    xml: str, expected_corp_id: str
) -> CustomerServiceEvent:
    event = parse_callback_event(xml, expected_corp_id)
    if event is None:
        raise CallbackParseError("callback is not a customer-service event")
    return event
