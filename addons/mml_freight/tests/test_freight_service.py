from odoo.tests.common import TransactionCase


class TestFreightService(TransactionCase):

    def test_service_is_registered_when_module_installed(self):
        """FreightService is accessible via the service locator after install."""
        from odoo.addons.mml_base.services.null_service import NullService
        svc = self.env['mml.registry'].service('freight')
        self.assertNotIsInstance(svc, NullService)

    def test_get_booking_lead_time_returns_none_for_missing(self):
        """get_booking_lead_time returns None for a non-existent booking ID."""
        svc = self.env['mml.registry'].service('freight')
        result = svc.get_booking_lead_time(999999)
        self.assertIsNone(result)

    def test_create_tender_returns_id(self):
        """create_tender returns an integer ID on success.

        freight.tender only requires company_id, which defaults to env.company,
        so an empty vals dict is sufficient for a minimal valid create.
        """
        svc = self.env['mml.registry'].service('freight')
        tender_id = svc.create_tender({})
        self.assertIsNotNone(tender_id)
        self.assertIsInstance(tender_id, int)
        # Confirm the record actually exists in the DB
        tender = self.env['freight.tender'].browse(tender_id)
        self.assertTrue(tender.exists())

    def test_create_tender_returns_none_on_failure(self):
        """create_tender returns None (not raises) when given invalid vals."""
        svc = self.env['mml.registry'].service('freight')
        # Pass a bad many2one value to force an ORM error
        result = svc.create_tender({'company_id': -9999})
        self.assertIsNone(result)
