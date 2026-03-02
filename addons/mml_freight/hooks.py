def post_init_hook(env):
    """Register mml_freight capabilities and service on install."""
    env['mml.capability'].register(
        [
            'freight.tender.create',
            'freight.booking.confirm',
            'freight.quote.request',
            'freight.booking.get_lead_time',
        ],
        module='mml_freight',
    )


def uninstall_hook(env):
    """Deregister all mml_freight entries on uninstall."""
    env['mml.capability'].deregister_module('mml_freight')
    env['mml.registry'].deregister('freight')
    env['mml.event.subscription'].deregister_module('mml_freight')
