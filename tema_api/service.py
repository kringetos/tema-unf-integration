"""PDF-compatible API routes and verified mappings for УНФ 3.0.2.215."""
import datetime
import re
import uuid
from decimal import Decimal, InvalidOperation

from .upstream import APIError, Result, literal


ENTITIES = {
    "counterparty": "Catalog_Контрагенты",
    "nomenclature": "Catalog_Номенклатура",
    "order": "Document_ЗаказПокупателя",
    "invoice": "Document_СчетНаОплату",
    "act": "Document_АктВыполненныхРабот",
}
PLURALS = {"counterparties": "counterparty", "nomenclatures": "nomenclature",
           "orders": "order", "invoices": "invoice", "acts": "act"}
ZERO = "00000000-0000-0000-0000-000000000000"
CURRENCIES = {"643": "RUB", "840": "USD", "978": "EUR", "156": "CNY", "826": "GBP"}


def bad(message):
    raise APIError(400, "VALIDATION_ERROR", message)


def integer(value, name, minimum=1, maximum=1000000000):
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        bad(f"{name}: требуется целое число")
    number = int(value)
    if not minimum <= number <= maximum:
        bad(f"{name}: допустимо от {minimum} до {maximum}")
    return number


def date(value, name):
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except (ValueError, TypeError):
        bad(f"{name}: требуется дата YYYY-MM-DD")


def money(value, name, positive=False):
    try:
        if isinstance(value, bool):
            raise ValueError()
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or (positive and number == 0):
            raise ValueError()
        return number
    except (InvalidOperation, ValueError):
        bad(f"{name}: требуется {'положительное' if positive else 'неотрицательное'} число")


def string(value, name, maximum=10000, empty=True):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        bad(f"{name}: требуется строка длиной до {maximum}")
    return value


class Service:
    def __init__(self, odata, bridge=None, settings=None):
        self.odata, self.bridge, self.settings = odata, bridge, settings or {}

    def bridge_call(self, method, path, query=None, body=None, key=None):
        if self.bridge is None:
            raise APIError(503, "BRIDGE_NOT_CONFIGURED", "Для этой операции требуется HTTP-расширение 1С",
                           "Задайте bridge_url и настройте расширение ТеМа")
        return self.bridge.request(method, path, query, body,
                                   {"Idempotency-Key": key} if key else None)

    def resolve(self, kind, identifier):
        string(identifier, "id", 100, empty=False)
        entity = ENTITIES[kind]
        field = "Code" if kind in {"counterparty", "nomenclature"} else "Number"
        try:
            guid = str(uuid.UUID(identifier))
        except ValueError:
            # Numbers are not guaranteed to be globally unique for documents.
            rows = self.odata.get(entity, {"$filter": f"{field} eq {literal(identifier)} and DeletionMark eq false",
                                          "$top": 2}).get("value", [])
            if len(rows) > 1:
                raise APIError(409, "AMBIGUOUS_ID", "Код или номер неоднозначен; используйте GUID")
            if not rows:
                raise APIError(404, "NOT_FOUND", "Объект не найден")
            return rows[0]
        obj = self.odata.get(f"{entity}(guid'{guid}')")
        if obj.get("DeletionMark") or obj.get("IsFolder"):
            raise APIError(404, "NOT_FOUND", "Объект не найден или недоступен")
        return obj

    def related(self, entity, key):
        if not key or key == ZERO:
            return {}
        try:
            key = str(uuid.UUID(key))
        except (ValueError, TypeError):
            raise APIError(502, "UPSTREAM_REFERENCE", "Некорректная ссылка в ответе 1С") from None
        return self.odata.get(f"{entity}(guid'{key}')")

    def currency_code(self, row):
        code = self.settings.get("currency_codes", {}).get(row.get("Code"), CURRENCIES.get(row.get("Code")))
        if not code:
            raise APIError(503, "CURRENCY_MAPPING_REQUIRED", "Настройте currency_codes для валюты " + row.get("Code", ""))
        return code

    def customer(self, row, detail=False):
        obj = self.related("Catalog_Контрагенты", row.get("Контрагент_Key"))
        result = {"id": obj.get("Code", ""), "name": obj.get("Description", "")}
        if detail:
            result["inn"] = obj.get("ИНН", "")
        return result

    def serialize(self, kind, row, detail=False):
        result = {"id": row.get("Code" if kind in {"counterparty", "nomenclature"} else "Number", ""),
                  "guid": row.get("Ref_Key", ""), "name": row.get("Description", "")}
        if kind == "counterparty":
            roles = {"is_customer": bool(row.get("Покупатель")), "is_supplier": bool(row.get("Поставщик")),
                     "is_other": bool(row.get("ПрочиеОтношения"))}
            tax = {"inn": row.get("ИНН", ""), "kpp": row.get("КПП", ""), "ogrn": row.get("РегистрационныйНомер", "")}
            result.update(type=row.get("ВидКонтрагента", ""), is_active=not row.get("Недействителен", False))
            if detail:
                result.update(full_name=row.get("НаименованиеПолное", ""), roles=roles, tax_info=tax,
                              contact_info=[{"type": {"АдресЭлектроннойПочты": "Электронная почта"}.get(r.get("Тип"), r.get("Тип", "")), "value": r.get("Представление", "")}
                                            for r in row.get("КонтактнаяИнформация", [])])
            else:
                result.update(inn=tax["inn"], kpp=tax["kpp"], is_customer=roles["is_customer"], is_supplier=roles["is_supplier"])
            return result
        if kind == "nomenclature":
            unit = self.related("Catalog_КлассификаторЕдиницИзмерения", row.get("ЕдиницаИзмерения_Key"))
            result.update(article=row.get("Артикул", ""), is_active=not row.get("Недействителен", False),
                          unit={"code": unit.get("Code", ""), "name": unit.get("Description", "")} if detail else unit.get("Description", ""))
            if detail:
                result.update(description=row.get("Комментарий", ""),
                              type={"Запас": "Товар"}.get(row.get("ТипНоменклатуры"), row.get("ТипНоменклатуры", "")))
            return result
        result.pop("name")
        result.update(number=row.get("Number", ""), date=row.get("Date", "")[:10],
                      customer=self.customer(row, detail), is_posted=bool(row.get("Posted")))
        amount = row.get("СуммаДокумента", 0)
        if kind == "order":
            state_key = row.get("СостояниеЗаказа")
            result.update(total_amount=amount, status=self.order_status(state_key))
        else:
            organization = self.related("Catalog_Организации", row.get("Организация_Key"))
            result.update(amount=amount, organization=organization.get("Description", ""),
                          links={"self": f"/api/v1/{kind}/{result['guid']}", "pdf": f"/api/v1/{kind}/{result['guid']}/pdf"})
        if kind == "invoice":
            currency = self.related("Catalog_Валюты", row.get("ВалютаДокумента_Key"))
            result["currency"] = self.currency_code(currency)
        if detail and kind in {"order", "invoice"}:
            items = []
            for part in ("Запасы", "Работы") if kind == "order" else ("Запасы",):
                for line in row.get(part, []):
                    product = self.related("Catalog_Номенклатура", line.get("Номенклатура_Key"))
                    item = {"id": line.get("LineNumber", len(items) + 1),
                            "product": {"id": product.get("Code", ""), "name": product.get("Description", "")},
                            "quantity": line.get("Количество", 0), "price": line.get("Цена", 0), "total": line.get("Всего", line.get("Сумма", 0))}
                    if kind == "order":
                        unit = self.related("Catalog_КлассификаторЕдиницИзмерения", product.get("ЕдиницаИзмерения_Key"))
                        item["unit"] = unit.get("Description", "")
                    if kind == "invoice":
                        unit = self.related("Catalog_КлассификаторЕдиницИзмерения", line.get("ЕдиницаИзмерения_Key"))
                        vat = self.related("Catalog_СтавкиНДС", line.get("СтавкаНДС_Key"))
                        item.update(unit=unit.get("Description", ""), vat=vat.get("Description", ""), vat_amount=line.get("СуммаНДС", 0))
                    items.append(item)
            result["items"] = items
            if kind == "invoice":
                result["vat_amount"] = float(sum((Decimal(str(x["vat_amount"])) for x in items), Decimal(0)))
        return result

    def order_status(self, key):
        reverse = {v: k for k, v in self.settings.get("order_statuses", {}).items()}
        if key and key != ZERO and key not in reverse:
            raise APIError(503, "STATUS_MAPPING_REQUIRED", "Настройте order_statuses для состояний заказов УНФ")
        return reverse.get(key, "new")

    def state_guid(self, status):
        value = self.settings.get("order_statuses", {}).get(status)
        if value is None and status == "new":
            return ZERO
        if value is None and status == "open":
            value = self.settings.get("order_statuses", {}).get("in_progress")
        if value is None:
            bad("Неизвестный или ненастроенный статус заказа")
        return str(uuid.UUID(value))

    def listing(self, kind, query):
        allowed = {"page", "limit", "search"}
        if kind in {"order", "invoice", "act"}:
            allowed |= {"date_from", "date_to", "customer_id"}
        if kind in {"order", "invoice"}:
            allowed.add("status")
        if kind == "invoice":
            allowed.add("lead_id")
        if set(query) - allowed:
            bad("Неизвестные параметры: " + ", ".join(sorted(set(query) - allowed)))
        page = integer(query.get("page", "1"), "page")
        limit = integer(query.get("limit", "50"), "limit", maximum=500)
        clauses = ["DeletionMark eq false"]
        if kind in {"counterparty", "nomenclature"}:
            clauses.append("IsFolder eq false")
        search = query.get("search", "")
        string(search, "search", 250)
        if search:
            fields = {"counterparty": ["Description", "ИНН"], "nomenclature": ["Description", "Артикул"]}.get(kind, ["Number"])
            clauses.append("(" + " or ".join(f"substringof({literal(search)}, {field}) eq true" for field in fields) + ")")
        for name, op in [("date_from", "ge"), ("date_to", "lt")]:
            if query.get(name):
                value = datetime.date.fromisoformat(date(query[name], name))
                if name == "date_to":
                    try:
                        value += datetime.timedelta(days=1)
                    except OverflowError:
                        bad("date_to: дата вне диапазона")
                clauses.append(f"Date {op} datetime'{value.isoformat()}T00:00:00'")
        if query.get("date_from") and query.get("date_to") and query["date_from"] > query["date_to"]:
            bad("date_from не может быть позже date_to")
        if query.get("customer_id"):
            customer = self.resolve("counterparty", query["customer_id"])
            clauses.append(f"Контрагент_Key eq guid'{customer['Ref_Key']}'")
        if kind == "invoice" and (query.get("status") or query.get("lead_id")):
            if query.get("status") not in {None, "", "paid", "unpaid", "partially", "partially_paid", "fully_paid"}:
                bad("Неизвестный статус оплаты")
            if query.get("lead_id"):
                integer(query["lead_id"], "lead_id")
            return self.bridge_call("GET", "/invoices", query)
        if kind == "order" and query.get("status"):
            state = self.state_guid(query["status"])
            # The live schema exposes this polymorphic reference as Edm.String.
            if state == ZERO:
                clauses.append(f"(СостояниеЗаказа eq '' or СостояниеЗаказа eq {literal(ZERO)})")
            else:
                clauses.append(f"СостояниеЗаказа eq {literal(state)} and СостояниеЗаказа_Type eq 'StandardODATA.Catalog_СостоянияЗаказовПокупателей'")
        data = self.odata.get(ENTITIES[kind], {"$filter": " and ".join(clauses),
                              "$top": limit, "$skip": (page - 1) * limit,
                              "$orderby": "Code,Ref_Key" if kind in {"counterparty", "nomenclature"} else "Date desc,Ref_Key",
                              "$inlinecount": "allpages"})
        if "odata.count" not in data and "__count" not in data:
            raise APIError(502, "COUNT_MISSING", "1С не вернула общее количество записей")
        return Result(200, {"total": int(data.get("odata.count", data.get("__count"))), "page": page,
                            "limit": limit, "data": [self.serialize(kind, row) for row in data.get("value", [])]})

    def validate_body(self, kind, body, creating=False):
        if not isinstance(body, dict):
            bad("Тело запроса должно быть JSON-объектом")
        allowed = {
            "counterparty": {"name", "full_name", "type", "inn", "kpp", "ogrn", "roles", "contact_info", "is_active"},
            "nomenclature": {"name", "article", "description", "unit", "type", "is_active"},
            "order": {"amocrm_lead_id", "date", "customer", "items", "comment", "status"},
            "invoice": {"order_id", "date", "responsible_person", "comment"},
            "act": {"amocrm_lead_id", "invoice_id", "date", "comment"},
            "payment": {"date", "amount", "currency", "payment_method", "description", "bank_account"},
        }[kind]
        if set(body) - allowed:
            bad("Неизвестные поля: " + ", ".join(sorted(set(body) - allowed)))
        required = {"counterparty": {"name", "type"}, "nomenclature": {"name", "type", "unit"},
                    "order": {"date", "customer", "items"}, "invoice": {"order_id", "date"},
                    "act": {"invoice_id", "date"}, "payment": {"date", "amount", "currency", "payment_method"}}[kind] if creating else set()
        if required - set(body):
            bad("Не заполнены поля: " + ", ".join(sorted(required - set(body))))
        if not body:
            bad("Пустой запрос")
        for field in {"name", "full_name", "article", "description", "inn", "kpp", "ogrn", "comment", "type", "status", "order_id", "invoice_id", "responsible_person", "currency", "payment_method", "bank_account"} & set(body):
            string(body[field], field, 10000 if field in {"description", "comment"} else 250,
                   empty=field not in {"name", "type", "order_id", "invoice_id"})
        for field, lengths in [("inn", {10, 12}), ("kpp", {9}), ("ogrn", {13, 15})]:
            if body.get(field) and (len(body[field]) not in lengths or not body[field].isdigit()):
                bad(f"Неверный формат {field}")
        if "date" in body:
            date(body["date"], "date")
        if "amocrm_lead_id" in body:
            integer(body["amocrm_lead_id"], "amocrm_lead_id")
        if "is_active" in body and not isinstance(body["is_active"], bool):
            bad("is_active: требуется true или false")
        if "roles" in body:
            roles = body["roles"]
            if not isinstance(roles, dict) or set(roles) - {"is_customer", "is_supplier", "is_other"} or any(not isinstance(v, bool) for v in roles.values()):
                bad("Некорректные roles")
        if "contact_info" in body:
            rows = body["contact_info"]
            if not isinstance(rows, list) or len(rows) > 100:
                bad("contact_info: требуется массив до 100 записей")
            for row in rows:
                if not isinstance(row, dict) or set(row) != {"type", "value"}:
                    bad("contact_info: ожидаются type и value")
                string(row["type"], "contact_info.type", 100, empty=False)
                string(row["value"], "contact_info.value", 1000, empty=False)
        if "unit" in body:
            unit = body["unit"]
            if not isinstance(unit, dict) or set(unit) - {"code", "name"} or not unit.get("code"):
                bad("unit: требуется объект с code")
            string(unit["code"], "unit.code", 20, empty=False)
        if "customer" in body:
            if not isinstance(body["customer"], dict) or set(body["customer"]) != {"id"}:
                bad("customer: требуется объект с id")
            string(body["customer"]["id"], "customer.id", 100, empty=False)
        if "items" in body:
            if not isinstance(body["items"], list) or not 1 <= len(body["items"]) <= 1000:
                bad("items: требуется от 1 до 1000 строк")
            for row in body["items"]:
                if not isinstance(row, dict) or set(row) != {"product_id", "quantity", "price"}:
                    bad("В строке items требуются product_id, quantity, price")
                string(row["product_id"], "product_id", 100, empty=False)
                money(row["quantity"], "quantity", positive=True)
                money(row["price"], "price")
        if kind == "payment" and "amount" in body:
            money(body["amount"], "amount", positive=True)

    def catalog_payload(self, kind, body):
        fields = {"name": "Description", "full_name": "НаименованиеПолное", "inn": "ИНН", "kpp": "КПП",
                  "ogrn": "РегистрационныйНомер", "article": "Артикул", "description": "Комментарий"}
        result = {target: body[source] for source, target in fields.items() if source in body}
        if "is_active" in body:
            result["Недействителен"] = not body["is_active"]
        if "roles" in body:
            for source, target in {"is_customer": "Покупатель", "is_supplier": "Поставщик", "is_other": "ПрочиеОтношения"}.items():
                if source in body["roles"]:
                    result[target] = body["roles"][source]
        if "type" in body:
            types = ({v: v for v in ["ЮридическоеЛицо", "ИндивидуальныйПредприниматель", "ФизическоеЛицо", "ГосударственныйОрган"]}
                     if kind == "counterparty" else {"Товар": "Запас", "Услуга": "Услуга", "Работа": "Работа"})
            if body["type"] not in types:
                bad("Неизвестный type")
            result["ВидКонтрагента" if kind == "counterparty" else "ТипНоменклатуры"] = types[body["type"]]
        if "unit" in body:
            unit = body["unit"]
            rows = self.odata.get("Catalog_КлассификаторЕдиницИзмерения", {"$filter": f"Code eq {literal(unit['code'])} and DeletionMark eq false", "$top": 2}).get("value", [])
            if len(rows) != 1:
                bad("Единица измерения не найдена однозначно по коду ОКЕИ")
            result["ЕдиницаИзмерения_Key"] = rows[0]["Ref_Key"]
        return result

    def catalog_write(self, kind, identifier, body, key=None):
        self.validate_body(kind, body, creating=identifier is None)
        payload = self.catalog_payload(kind, body)
        # BSP contact records need their kind and structured values; keep the whole
        # operation in one 1C transaction instead of partially writing through OData.
        if "contact_info" in body:
            if not self.settings.get("contact_writes_enabled", False):
                raise APIError(503, "CONTACT_WRITES_DISABLED", "Запись контактов временно отключена после сбоя платформы 1С",
                               "Остальные поля контрагента доступны через OData")
            return self.bridge_call("POST" if identifier is None else "PATCH",
                                    f"/{kind}" + (f"/{identifier}" if identifier else ""), body=body, key=key)
        obj = self.resolve(kind, identifier) if identifier else None
        effective_inn = body.get("inn", obj.get("ИНН", "") if obj else "")
        if kind == "counterparty" and effective_inn and ({"inn", "kpp"} & set(body)):
            clauses = [f"ИНН eq {literal(effective_inn)}", "DeletionMark eq false", "IsFolder eq false"]
            if body.get("kpp") or (obj and obj.get("КПП")):
                clauses.append(f"КПП eq {literal(body.get('kpp', obj.get('КПП', '') if obj else ''))}")
            if obj:
                clauses.append(f"Ref_Key ne guid'{obj['Ref_Key']}'")
            if self.odata.get(ENTITIES[kind], {"$filter": " and ".join(clauses), "$top": 1}).get("value"):
                raise APIError(409, "DUPLICATE", "Контрагент с таким ИНН и КПП уже существует")
        if obj:
            headers = {"If-Match": "W/\"" + obj["DataVersion"] + "\""} if obj.get("DataVersion") else None
            self.odata.request("PATCH", f"{ENTITIES[kind]}(guid'{obj['Ref_Key']}')", body=payload, headers=headers)
            return Result(200, {"status": "success", "data": {"id": obj["Code"], "guid": obj["Ref_Key"]}})
        created = self.odata.request("POST", ENTITIES[kind], body=payload).body
        return Result(201, {"status": "success", "message": "Объект успешно создан",
                            "data": {"id": created["Code"], "guid": created["Ref_Key"]}})

    def dispatch(self, method, path, query=None, body=None, key=None):
        query = query or {}
        if path == "/health" and method == "GET":
            return Result(200, {"status": "ok", "bridge_configured": self.bridge is not None})
        path = path.rstrip("/")
        for prefix in ("/1cTema/hs/api/v1", "/api/v1"):
            if path.startswith(prefix + "/"):
                path = path[len(prefix):]
                break
        parts = path.strip("/").split("/")
        if len(parts) == 1 and parts[0] in PLURALS:
            if method != "GET":
                raise APIError(405, "METHOD_NOT_ALLOWED", "Метод не поддерживается")
            return self.listing(PLURALS[parts[0]], query)
        if parts[0] not in ENTITIES or len(parts) > 4:
            raise APIError(404, "NOT_FOUND", "Маршрут не найден")
        kind = parts[0]
        if len(parts) == 1 and method == "POST":
            if kind in {"counterparty", "nomenclature"}:
                return self.catalog_write(kind, None, body, key)
            self.validate_body(kind, body, creating=True)
            if kind == "order":
                organization = self.settings.get("defaults", {}).get("organization_guid")
                if organization:
                    body = {**body, "_organization_guid": str(uuid.UUID(organization))}
            if kind == "order" and "status" in body:
                body = {**body, "_state_guid": self.state_guid(body["status"])}
            return self.bridge_call(method, path, body=body, key=key)
        if len(parts) == 2:
            identifier = parts[1]
            if method == "GET":
                if query:
                    bad("Этот маршрут не принимает параметры")
                if kind in {"invoice", "act", "order"}:
                    # Includes accounting payment state, source invoice and lead mapping.
                    # The bridge returns the complete PDF contract; no fabricated fields.
                    result = self.bridge_call(method, path)
                    if kind == "order" and isinstance(result.body, dict) and "_state" in result.body:
                        result.body["status"] = self.order_status(result.body.pop("_state"))
                    return result
                return Result(200, self.serialize(kind, self.resolve(kind, identifier), detail=True))
            if method == "PATCH" and kind != "act":
                if kind in {"counterparty", "nomenclature"}:
                    return self.catalog_write(kind, identifier, body, key)
                self.validate_body(kind, body)
                if kind == "order" and "status" in body:
                    body = {**body, "_state_guid": self.state_guid(body["status"])}
                return self.bridge_call(method, path, body=body, key=key)
        pdf = ((kind in {"invoice", "act"} and len(parts) == 3 and parts[2] == "pdf") or
               (kind == "order" and len(parts) == 4 and parts[2] in {"kp", "specification"} and parts[3] == "pdf"))
        if method == "GET" and pdf:
            if kind == "order" and parts[2] == "specification":
                from .printing import specification
                row = self.resolve("order", parts[1])
                order = self.serialize(kind, row, detail=True)
                currency = self.related("Catalog_Валюты", row.get("ВалютаДокумента_Key"))
                organization = self.related("Catalog_Организации", row.get("Организация_Key"))
                raw = specification(order, self.currency_code(currency), organization.get("Description", ""))
                return Result(200, raw, "application/pdf", "inline; filename=specification.pdf")
            query = None
            if kind == "order":
                command = self.settings.get("print_commands", {}).get(parts[2])
                if not command:
                    raise APIError(503, "PRINT_MAPPING_REQUIRED", "Настройте команду печати " + parts[2])
                query = {"command": command}
            return self.bridge_call(method, path, query=query)
        if kind == "invoice" and len(parts) == 3 and parts[2] == "payment" and method == "POST":
            self.validate_body("payment", body, creating=True)
            return self.bridge_call(method, path, body=body, key=key)
        if len(parts) <= 2 or pdf or (kind == "invoice" and parts[2:] == ["payment"]):
            raise APIError(405, "METHOD_NOT_ALLOWED", "Метод не поддерживается")
        raise APIError(404, "NOT_FOUND", "Маршрут не найден")
