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
        """Return delivery.carrier records eligible for the given tender."""
        all_carriers = self.env['delivery.carrier'].search([
            ('active', '=', True),
            ('auto_tender', '=', True),
        ])
        eligible = self.env['delivery.carrier']
        for carrier in all_carriers:
            if carrier.is_eligible(
                tender.origin_country_id,
                tender.dest_country_id,
                tender.chargeable_weight_kg,
                tender.contains_dg,
                tender.freight_mode_preference or 'any',
            ):
                eligible |= carrier
        return eligible
