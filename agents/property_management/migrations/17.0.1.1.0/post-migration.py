def migrate(cr, version):
    """Rent products must be sellable, otherwise they do not show on invoice lines."""
    cr.execute(
        """
        UPDATE product_template pt
           SET sale_ok = TRUE
          FROM product_product pp
          JOIN pm_unit_type ut ON ut.product_id = pp.id
         WHERE pp.product_tmpl_id = pt.id
        """
    )
