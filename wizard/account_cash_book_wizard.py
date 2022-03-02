# -*- encoding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import date, datetime
import json
import io
from odoo.tools import date_utils
try:
    from odoo.tools.misc import xlsxwriter
except ImportError:
    import xlsxwriter


class CashBookWizard(models.TransientModel):
    _name = 'account.report.cash.book'

    company_id = fields.Many2one('res.company', string='Company', readonly=True,
                                 default=lambda self: self.env.user.company_id)
    target_move = fields.Selection([('posted', 'All Posted Entries'),
                                    ('all', 'All Entries'),
                                    ], string='Target Moves', required=True, default='posted')

    date_from = fields.Date(string='Start Date', default=date.today(), requred=True)
    date_to = fields.Date(string='End Date', default=date.today(), requred=True)
    display_account = fields.Selection([('all', 'All'), ('movement', 'With movements'),
                                        ('not_zero', 'With balance is not equal to 0'), ],
                                       string='Display Accounts', required=True, default='movement')
    sortby = fields.Selection([('sort_date', 'Date'), ('sort_journal_partner', 'Journal & Partner')], string='Sort by',
                              required=True, default='sort_date')
    initial_balance = fields.Boolean(string='Include Initial Balances',
                                     help='If you selected date, this field allow you to add a row to display the amount of debit/credit/balance that precedes the filter you\'ve set.')

    def _get_default_account_ids(self):
        journals = self.env['account.journal'].search(['|', ('type', '=', 'cash'), ('type', '=', 'bank')])
        accounts = []
        for journal in journals:
            accounts.append(journal.default_credit_account_id.id)
        return accounts

    account_ids = fields.Many2many('account.account', 'account_report_cashbook_account_rel', 'report_id', 'account_id',
                                   'Accounts', default=_get_default_account_ids)
    journal_ids = fields.Many2many('account.journal', 'account_report_cashbook_journal_rel', 'account_id', 'journal_id', string='Journals', required=True, default=lambda self: self.env['account.journal'].search([]))

    @api.onchange('account_ids')
    def onchange_account_ids(self):
        if self.account_ids:
            journals = self.env['account.journal'].search(['|', ('type', '=', 'cash'), ('type', '=', 'bank')])
            accounts = []
            for journal in journals:
                accounts.append(journal.default_credit_account_id.id)
            domain = {'account_ids': [('id', 'in', accounts)]}
            return {'domain': domain}

    def _build_contexts(self, data):
        result = {}
        result['journal_ids'] = 'journal_ids' in data['form'] and data['form']['journal_ids'] or False
        result['state'] = 'target_move' in data['form'] and data['form']['target_move'] or ''
        result['date_from'] = data['form']['date_from'] or False
        result['date_to'] = data['form']['date_to'] or False
        result['strict_range'] = True if result['date_from'] else False
        return result

    @api.multi
    def check_report(self):
        self.ensure_one()
        if self.initial_balance and not self.date_from:
            raise UserError(_("You must choose a Start Date"))
        data = {}
        data['ids'] = self.env.context.get('active_ids', [])
        data['model'] = self.env.context.get('active_model', 'ir.ui.menu')
        data['form'] = self.read(['date_from', 'date_to', 'journal_ids', 'target_move', 'display_account',
                                  'account_ids', 'sortby', 'initial_balance'])[0]
        used_context = self._build_contexts(data)
        data['form']['used_context'] = dict(used_context, lang=self.env.context.get('lang') or 'en_US')
        return self.env.ref('account_cash_book.action_report_cash_book').report_action(self, data=data)

    @api.multi
    def report_xlsx(self):
        self.ensure_one()
        data = {}
        data['ids'] = self.env.context.get('active_ids', [])
        data['model'] = self.env.context.get('active_model', 'ir.ui.menu')
        data['form'] = self.read(['date_from', 'initial_balance', 'date_to', 'sortby', 'account_ids', 'display_account', 'journal_ids', 'target_move'])[0]
        used_context = self._build_contexts(data)
        data['form']['used_context'] = dict(used_context, lang=self.env.context.get('lang') or 'en_US')
        return {
            'type': 'ir_actions_xlsx_download',
            'data': {'model': 'account.report.cash.book',
                     'options': json.dumps(data, default=date_utils.json_default),
                     'output_format': 'xlsx',
                     'report_name': 'cash book report',
                     }
        }

    def _get_account_move_entry(self, accounts, init_balance, sortby, display_account):

        cr = self.env.cr
        MoveLine = self.env['account.move.line']
        move_lines = {x: [] for x in accounts.ids}

        # Prepare initial sql query and Get the initial move lines
        if init_balance:
            init_tables, init_where_clause, init_where_params = MoveLine.with_context(
                date_from=self.env.context.get('date_from'), date_to=False, initial_bal=True)._query_get()
            init_wheres = [""]
            if init_where_clause.strip():
                init_wheres.append(init_where_clause.strip())
            init_filters = " AND ".join(init_wheres)
            filters = init_filters.replace('account_move_line__move_id', 'm').replace('account_move_line', 'l')
            sql = ("""SELECT 0 AS lid, l.account_id AS account_id, '' AS ldate, '' AS lcode, 0.0 AS amount_currency, '' AS lref, 'Initial Balance' AS lname, COALESCE(SUM(l.debit),0.0) AS debit, COALESCE(SUM(l.credit),0.0) AS credit, COALESCE(SUM(l.debit),0) - COALESCE(SUM(l.credit), 0) as balance, '' AS lpartner_id,\
                    '' AS move_name, '' AS mmove_id, '' AS currency_code,\
                    NULL AS currency_id,\
                    '' AS invoice_id, '' AS invoice_type, '' AS invoice_number,\
                    '' AS partner_name\
                    FROM account_move_line l\
                    LEFT JOIN account_move m ON (l.move_id=m.id)\
                    LEFT JOIN res_currency c ON (l.currency_id=c.id)\
                    LEFT JOIN res_partner p ON (l.partner_id=p.id)\
                    LEFT JOIN account_invoice i ON (m.id =i.move_id)\
                    JOIN account_journal j ON (l.journal_id=j.id)\
                    WHERE l.account_id IN %s""" + filters + ' GROUP BY l.account_id')
            params = (tuple(accounts.ids),) + tuple(init_where_params)
            cr.execute(sql, params)
            for row in cr.dictfetchall():
                move_lines[row.pop('account_id')].append(row)

        sql_sort = 'l.date, l.move_id'
        if sortby == 'sort_journal_partner':
            sql_sort = 'j.code, p.name, l.move_id'

        # Prepare sql query base on selected parameters from wizard
        tables, where_clause, where_params = MoveLine._query_get()
        wheres = [""]
        if where_clause.strip():
            wheres.append(where_clause.strip())
        filters = " AND ".join(wheres)
        filters = filters.replace('account_move_line__move_id', 'm').replace('account_move_line', 'l')

        # Get move lines base on sql query and Calculate the total balance of move lines
        sql = ('''SELECT l.id AS lid, l.account_id AS account_id, l.date AS ldate, j.code AS lcode, l.currency_id, l.amount_currency, l.ref AS lref, l.name AS lname, COALESCE(l.debit,0) AS debit, COALESCE(l.credit,0) AS credit, COALESCE(SUM(l.debit),0) - COALESCE(SUM(l.credit), 0) AS balance,\
                m.name AS move_name, c.symbol AS currency_code, p.name AS partner_name\
                FROM account_move_line l\
                JOIN account_move m ON (l.move_id=m.id)\
                LEFT JOIN res_currency c ON (l.currency_id=c.id)\
                LEFT JOIN res_partner p ON (l.partner_id=p.id)\
                JOIN account_journal j ON (l.journal_id=j.id)\
                JOIN account_account acc ON (l.account_id = acc.id) \
                WHERE l.account_id IN %s ''' + filters + ''' GROUP BY l.id, l.account_id, l.date, j.code, l.currency_id, l.amount_currency, l.ref, l.name, m.name, c.symbol, p.name ORDER BY ''' + sql_sort)
        params = (tuple(accounts.ids),) + tuple(where_params)
        cr.execute(sql, params)

        for row in cr.dictfetchall():
            balance = 0
            for line in move_lines.get(row['account_id']):
                balance += line['debit'] - line['credit']
            row['balance'] += balance
            move_lines[row.pop('account_id')].append(row)

        # Calculate the debit, credit and balance for Accounts
        account_res = []
        for account in accounts:
            currency = account.currency_id and account.currency_id or account.company_id.currency_id
            res = dict((fn, 0.0) for fn in ['credit', 'debit', 'balance'])
            res['code'] = account.code
            res['name'] = account.name
            res['move_lines'] = move_lines[account.id]
            for line in res.get('move_lines'):
                res['debit'] += line['debit']
                res['credit'] += line['credit']
                res['balance'] = line['balance']
            if display_account == 'all':
                account_res.append(res)
            if display_account == 'movement' and res.get('move_lines'):
                account_res.append(res)
            if display_account == 'not_zero' and not currency.is_zero(res['balance']):
                account_res.append(res)

        return account_res

    def get_xlsx_report(self, options, response):
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        if not options.get('form'):
            raise UserError(_("Form content is missing, this report cannot be printed."))
        #
        # self.model = self.env.context.get('active_model')
        # docs = self.env[self.model].browse(self.env.context.get('active_ids', []))
        #
        init_balance = options['form'].get('initial_balance', True)
        sortby = options['form'].get('sortby', 'sort_date')
        display_account = options['form']['display_account']
        codes = []
        if options['form'].get('journal_ids', False):
            codes = [journal.code for journal in
                     self.env['account.journal'].search([('id', 'in', options['form']['journal_ids'])])]
        #
        account_ids = options['form']['account_ids']
        accounts = self.env['account.account'].search([('id', 'in', account_ids)])
        report_obj = self.with_context(options['form'].get('used_context', {}))._get_account_move_entry(accounts,
                                                                                                       init_balance,
                                                                                                       sortby,
                                                                                                       display_account)
        sheet = workbook.add_worksheet()
        format1 = workbook.add_format({'font_size': 16, 'align': 'center', 'bg_color': '#D3D3D3', 'bold': True})
        format2 = workbook.add_format({'font_size': 12, 'bold': True, 'bg_color': '#D3D3D3'})
        format3 = workbook.add_format({'font_size': 10, 'bold': True})
        format4 = workbook.add_format({'font_size': 10})
        format6 = workbook.add_format({'font_size': 10, 'bold': True})
        format7 = workbook.add_format({'font_size': 10, 'align': 'center'})
        format5 = workbook.add_format({'font_size': 10, 'align': 'right'})

        format1.set_align('center')
        format2.set_align('center')
        format3.set_align('right')
        format4.set_align('left')
        codes = [journal.code for journal in
                 self.env['account.journal'].search([('id', 'in', options['form']['journal_ids'])])]
        logged_users = self.env['res.company']._company_default_get('account.account')
        report_date = datetime.now().strftime("%Y-%m-%d")
        sheet.merge_range(0, 5, 0, 8, logged_users.name, format3)
        sheet.merge_range(0, 0, 0, 4, "Report Date : " + report_date, format6)
        sheet.merge_range(1, 0, 2, 8, "Cash Book Report", format1)

        journal_codes = ''
        for code in codes:
            journal_codes += code
            if journal_codes:
                journal_codes += ', '
        sheet.write('A4', "Journals : ", format6)
        sheet.merge_range(3, 1, 3, 8, journal_codes, format4)

        if options['form']['target_move'] == 'all':
            target_moves = 'All entries'
        else:
            target_moves = 'All posted entries'

        if options['form']['sortby'] == 'sort_date':
            sortby = 'Date'
        else:
            sortby = 'Journal and partners'
        if options['form']['date_from']:
            date_start = options['form']['date_from']
        else:
            date_start = ""
        if options['form']['date_to']:
            date_end = options['form']['date_to']
        else:
            date_end = ""
        if sortby == 'Date':
            sheet.write('G5', "Start Date", format3)
            sheet.write('G6', date_start, format4)
            sheet.write('I5', "End Date", format3)
            sheet.write('I6', date_end, format4)
        sheet.write('A5', "Sorted By", format6)
        sheet.write('A6', sortby, format4)
        sheet.write('C5', "Target Moves", format6)
        sheet.write('C6', target_moves, format4)

        sheet.write('A8', "Date ", format2)
        sheet.write('B8', "JRNL", format2)
        sheet.write('C8', "Partner", format2)
        sheet.write('D8', "Ref", format2)
        sheet.write('E8', "Move", format2)
        sheet.write('F8', "Entry Label", format2)
        sheet.write('G8', "Debit", format2)
        sheet.write('H8', "Credit", format2)
        sheet.write('I8', "Balance", format2)
        accounts = self.env['account.account'].search([])
        row_number = 8
        col_number = 0
        for datas in accounts:
            for values in report_obj:
                if datas['name'] == values['name']:
                    sheet.write(row_number, col_number, datas['code'] + ' ' + datas['name'], format6)
                    sheet.write(row_number, col_number + 6, values['debit'], format3)
                    sheet.write(row_number, col_number + 7, values['credit'], format3)
                    sheet.write(row_number, col_number + 8, values['balance'], format3)
                    row_number += 1
                    for lines in values['move_lines']:
                        sheet.write(row_number, col_number, lines['ldate'], format4)
                        sheet.write(row_number, col_number + 1, lines['lcode'], format4)
                        sheet.write(row_number, col_number + 2, lines['partner_name'], format4)
                        sheet.write(row_number, col_number + 3, lines['lref'], format4)
                        sheet.write(row_number, col_number + 4, lines['move_name'], format4)
                        sheet.write(row_number, col_number + 5, lines['lname'], format4)
                        sheet.write(row_number, col_number + 6, lines['debit'], format5)
                        sheet.write(row_number, col_number + 7, lines['credit'], format5)
                        sheet.write(row_number, col_number + 8, lines['balance'], format5)
                        row_number += 1
        workbook.close()
        output.seek(0)
        response.stream.write(output.read())
        output.close()