HOMEPAGE_ARCH = """<t name="Homepage" t-name="website.homepage">
    <t t-call="website.layout">
        <t t-set="pageName" t-value="'homepage'"/>
        <div id="wrap" class="oe_structure">
            <section class="s_text_block" style="background: linear-gradient(135deg, #0f766e 0%, #115e59 55%, #134e4a 100%); color: #ffffff; min-height: 70vh;">
                <div class="container text-center py-5" style="max-width: 940px;">
                    <h1 class="display-3 fw-bold pt-5">Welcome home</h1>
                    <p class="lead pb-5" style="opacity: 0.85;">Apartment management made simple. Sign in to run your buildings, or to see your rent and invoices.</p>
                    <div class="row g-4 justify-content-center pb-5">
                        <div class="col-12 col-md-6">
                            <a href="/web/login" class="card h-100 border-0 shadow-lg text-decoration-none">
                                <div class="card-body p-4 p-md-5">
                                    <i class="fa fa-briefcase fa-3x mb-3" style="color: #0f766e;"/>
                                    <h2 class="h3" style="color: #134e4a;">Staff portal</h2>
                                    <p class="text-muted mb-4">For managers, accountants and caretakers. Sign in with your email and password.</p>
                                    <span class="btn btn-lg w-100 text-white" style="background-color: #0f766e;">Staff sign in</span>
                                </div>
                            </a>
                        </div>
                        <div class="col-12 col-md-6">
                            <a href="/tenant/login" class="card h-100 border-0 shadow-lg text-decoration-none">
                                <div class="card-body p-4 p-md-5">
                                    <i class="fa fa-home fa-3x mb-3" style="color: #0f766e;"/>
                                    <h2 class="h3" style="color: #134e4a;">Tenant portal</h2>
                                    <p class="text-muted mb-4">For tenants. Sign in with your name, your building and your password.</p>
                                    <span class="btn btn-lg w-100 text-white" style="background-color: #0f766e;">Tenant sign in</span>
                                </div>
                            </a>
                        </div>
                    </div>
                </div>
            </section>
        </div>
    </t>
</t>"""


def apply_website_branding(env):
    """One-time website make-over: our homepage, no 'Contact us' menu, a friendlier site name."""
    views = env["ir.ui.view"].with_context(no_cow=True, active_test=False).search([("key", "=", "website.homepage")])
    views.write({"arch": HOMEPAGE_ARCH})
    env["website.menu"].search([("url", "=", "/contactus")]).unlink()
    env["website"].search([("name", "=", "My Website")]).write({"name": "Property Management"})


def post_init_hook(env):
    """Give the first real user every Property Management role (one-man-army default)."""
    category = env.ref("property_management.module_category_property_management")
    groups = env["res.groups"].search([("category_id", "=", category.id)])
    first_user = env["res.users"].search([("share", "=", False)], order="id", limit=1)
    if first_user:
        first_user.write({"groups_id": [(4, group.id) for group in groups]})
    apply_website_branding(env)
