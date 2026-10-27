def migrate(cr, version):
    """Rent invoicing was removed. Drop the invoice tab we had added to Odoo's invoice form."""
    cr.execute(
        """
        DELETE FROM ir_ui_view WHERE id IN (
            SELECT res_id FROM ir_model_data
             WHERE module = 'property_management' AND model = 'ir.ui.view'
               AND name = 'pm_account_move_view_form')
        """
    )
