from odoo import models, api
import logging

_logger = logging.getLogger(__name__)

_ADAPTER_REGISTRY = {}


def register_adapter(delivery_type):
    """Decorator: register a FreightAdapterBase subclass for a delivery_type.

    Usage in adapter modules:
        @register_adapter('dsv_generic')
        class DsvGenericAdapter(FreightAdapterBase):
            ...
    """
    def decorator(cls):
        _ADAPTER_REGISTRY[delivery_type] = cls
        _logger.info('Freight adapter registered: %s -> %s', delivery_type, cls.__name__)
        return cls
    return decorator


class FreightAdapterRegistry(models.AbstractModel):
    _name = 'freight.adapter.registry'
    _description = 'Freight Adapter Registry'

    @api.model
    def get_adapter(self, carrier):
        """Return an instantiated adapter for the given delivery.carrier record.

        Returns None if no adapter is registered for the carrier's delivery_type.
        """
        delivery_type = carrier.delivery_type
        cls = _ADAPTER_REGISTRY.get(delivery_type)
        if not cls:
            _logger.warning('No freight adapter registered for delivery_type: %s', delivery_type)
            return None
        return cls(carrier, self.env)

    @api.model
    def get_eligible_carriers(self, tender):
        """Return delivery.carrier records eligible for the given tender.

        Carriers whose adapter is a known stub (``adapter.is_stub``) are skipped:
        a stub raises on every operation, so including one would blow up the tender
        mid-fan-out. This keeps a half-built carrier (e.g. K+N in production mode)
        from being tendered to even if someone enables it.
        """
        all_carriers = self.env['delivery.carrier'].search([
            ('active', '=', True),
            ('auto_tender', '=', True),
        ])
        eligible = self.env['delivery.carrier']
        for carrier in all_carriers:
            if not carrier.is_eligible(
                tender.origin_country_id,
                tender.dest_country_id,
                tender.chargeable_weight_kg,
                tender.contains_dg,
                tender.freight_mode_preference or 'any',
            ):
                continue
            adapter = self.get_adapter(carrier)
            if adapter is None or getattr(adapter, 'is_stub', False):
                if adapter is not None:
                    _logger.warning(
                        'Skipping carrier %s (%s): adapter is a stub — not tendering.',
                        carrier.name, carrier.delivery_type,
                    )
                continue
            eligible |= carrier
        return eligible
