
# -*- coding: utf-8 -*-
{
    'name': 'Cash Book Report',
    'version': '12.0.2.0.1',
    'summary': 'Generates cash book report in both PDF and XLSX formats.',
    'description': """Generates cash book report in both PDF and XLSX formats.""",
    'category': 'Accounting',
    'author': 'Cybrosys Techno Solutions',
    'company': 'Cybrosys Techno Solutions',
    'maintainer': 'Cybrosys Techno Solutions',
    'depends': ['base', 'account'],
    'website': 'https://www.cybrosys.com',
    'data': [
        'wizard/account_cash_book_wizard_view.xml',
        'views/account_cash_book_report_view.xml',
        'views/action_manager.xml',
        'views/account_cash_book_template_view.xml'
    ],
    'qweb': [],
    'images': ['static/description/banner.jpg'],
    'license': 'OPL-1',
    'price': 9.99,
    'currency': 'EUR',
    'installable': True,
    'auto_install': False,
    'application': False,
}
