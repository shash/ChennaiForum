#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright 2009 Andris Reinman (http://www.turbinecms.com, http://www.andrisreinman.com)
#
# Permission is hereby granted, free of charge, to any person obtaining
#/ a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#
# For details, see the TurbineCMS web site: http://www.turbinecms.com/

########################### IMPORT DECLARATIONS ###########################

# HTTP related 
import wsgiref.handlers
from google.appengine.ext import webapp

# Storage
from google.appengine.ext import db
from google.appengine.api import memcache

# Views
from google.appengine.ext.webapp import template
from django.template import Context, Template # for custom templates
from django.template.loader import render_to_string

# System
import os
import re
from datetime import datetime, date, timedelta
from google.appengine.api import users

# Helpers
from django.utils import simplejson as json
import urllib
import logging
from google.appengine.api import images


########################### DATABASE DEFINITIONS ###########################

# Setting table. Holds site-wide settings as name-value pairs
# as this information does not need to be indexed in any way, then most of the
# values can be json encoded strings (to hold more than one property in one row)

class Setting(db.Model):
  name = db.StringProperty()
  value = db.TextProperty()

# Page table holds page contents
class Page(db.Model):
  title = db.StringProperty()
  url = db.StringProperty() # clean url title like 'about' for 'About' etc.
  content = db.TextProperty()
  draft = db.BooleanProperty(default=True)
  owner = db.SelfReferenceProperty()
  created = db.DateTimeProperty(auto_now_add=True)
  edited = db.DateTimeProperty(auto_now=True)

class Media(db.Model):
  name = db.StringProperty()
  type = db.StringProperty()
  description = db.StringProperty()
  file = db.BlobProperty()
  thumbnail = db.BlobProperty()
  width = db.IntegerProperty()
  height = db.IntegerProperty()
  uploaded = db.DateTimeProperty(auto_now_add = True)

########################### HELPER FUNCTIONS ###########################

# get_site_prefs()
# @return Array
# function retrieves site preferences as an array (title, description etc.)

def get_site_prefs():
  site_prefs= memcache.get("site-prefs")
  if site_prefs is not None:
    return site_prefs
  else:

    defaults = {
        'title': u'TurbineCMS',
        'description': u'TurbineCMS is a lightweight CMS designed to run on Google App Engine',
        'front': False,
        'templateDefault': True,
        'templateText': False
    }
    
    file = open('views/base.html')
    defaults['templateText'] = file.read()
    file.close()

    site_prefs = False
    query = db.GqlQuery("SELECT * FROM Setting WHERE name = :1", "site_prefs")
    for sp in query:
      try:
        site_prefs = sp.value and json.loads(sp.value) or defaults
      except:
        site_prefs = defaults
    if not site_prefs:
      site_prefs = defaults
      s = Setting()
      s.name = 'site_prefs'
      s.value = json.dumps(site_prefs)
      s.put()

    memcache.set("site-prefs", site_prefs)
    return site_prefs

# set_site_prefs()
# @param site_prefs Array
# function saves site preferences to database and memcache

def set_site_prefs(site_prefs):
  query = db.GqlQuery("SELECT * FROM Setting WHERE name = :1", "site_prefs")
  s = False
  for sp in query:
    try:
      s = sp
    except:
      s = False
  if not s:
    s = Setting()
  s.name = 'site_prefs'
  s.value = json.dumps(site_prefs)
  s.put()
  memcache.set("site-prefs", site_prefs)
  memcache.delete('feed')

# error_404()
# @param self Object
# function shows error 404 page

def error_404(self):
  self.response.set_status(404)
  
  site_prefs = get_site_prefs()
  
  template_values = {
    'site_title': site_prefs['title'],
    'description': site_prefs['description'],
    'title': u'Not Found',
    'content': u'The requested URL %s was not found on this server.' % self.request.path,
    'links': get_links()
  }
  
  try:
    if not site_prefs['templateDefault'] and len(site_prefs['templateText'].strip()):
      self.response.out.write(Template(site_prefs['templateText']).render(path, template_values))
      return
  except:
    pass
  path = os.path.join(os.path.dirname(__file__), 'views/base.html')
  self.response.out.write(template.render(path, template_values))


# get_page()
# @param url String
# @return db.Object
# function takes url identifier and retrieves corresponding row from the database

def get_page(url):
  page = memcache.get("page-%s" % url)
  if page is None:
    page = False
    query = db.GqlQuery("SELECT * FROM Page WHERE url = :1", url)
    for p in query:
      page = p
      memcache.set("page-%s" % url, page)
  return page

# get_unique_url()
# @param url String
# @return String
# function takes in an url and checks if it's already used.
# If the url already exists then adds a number to the end of the url

def get_unique_url(url):
  nr = 0
  t_url = url
  page = get_page(url)
  while page:
    nr += 1
    t_url = "%s-%s" % (url, nr)
    page = get_page(t_url)
  return t_url

# get_links()
# @return Array
# function retrieves alphabetically sorted list of active pages for the site menu

def get_links():
  links = memcache.get('site-links')
  if links is None:
    links = []
    site_prefs = get_site_prefs()
    query = Page.all()
    query.order("title")
    pages = query.fetch(1000)
    for page in pages:
      if not page.draft and not page.owner and (not site_prefs['front'] or site_prefs['front']!=page.url):
        links.append({'title':page.title,'url':page.url,'key':str(page.key())})

    memcache.set("site-links", links)
  return links


########################### VIEW HANDLERS ###########################

# PageHandler
# Main handler, displays the frontpage and all other CMS pages

class PageHandler(webapp.RequestHandler):
  def get(self, url=False):
    
    #Load site prefs
    site_prefs = get_site_prefs()
    
    if not url and site_prefs['front']:
      url = site_prefs['front']
    
    # Load current page
    page = get_page(url)
    
    if page:
      # Load subpages
      subpages = memcache.get('subpage-%s' % str(page.key()))
      if subpages is None:
        q = Page.all()
        q.filter("owner =", page)
        q.order("-created")
        subpages = q.fetch(1000)
        memcache.set('subpage-%s' % str(page.key()), subpages)
    
    if not page or page.draft:
      return error_404(self)

    #Render page
    template_values = {
        'site_title': site_prefs['title'],
        'description': site_prefs['description'],
        'page':page,
        'subpages': subpages,
        'links': get_links()
    }

    try:
      if not site_prefs.get('templateDefault',False) and site_prefs.get('templateText', False):
        t = Template(site_prefs['templateText'].encode('utf-8'))
        c = Context(template_values)
        tmpl = t.render(c)
        self.response.out.write(tmpl)
        return
    except:
      logging.debug('Template error')
      pass
    path = os.path.join(os.path.dirname(__file__), 'views/base.html')
    self.response.out.write(template.render(path, template_values))


# FeedHandler
# Handler for RSS feed, displays the last 10 added pages

class FeedHandler(webapp.RequestHandler):
  def get(self, url=False):
    
    #Load site prefs
    site_prefs = get_site_prefs()

    # Sat, 08 Aug 2009 12:57:53 +0000
    fmt = '%a, %d %b %Y %H:%M:%S +0000'

    items = memcache.get('feed')
    if items is None:
      items = []
      query = db.GqlQuery("SELECT * FROM Page WHERE draft = :1 ORDER BY created DESC", False)
      for page in query:
        item = {
            'title':page.title,
            'content': page.content,
            'url': page.url,
            'date':page.created.strftime(fmt)
        }
        items.append(item)
      memcache.set('feed',items)

    if len(items):
      pubdate = items[0]['date']
    else:
      pubdate = datetime.utcnow().strftime(fmt)

    template_values = {
        'title': site_prefs['title'],
        'description': site_prefs['description'],
        'domain':os.environ['HTTP_HOST'],
        'pubdate': pubdate,
        'items':items
    }
    
    self.response.headers['Content-Type'] = 'application/rss+xml; Charset=utf-8'

    path = os.path.join(os.path.dirname(__file__), 'views/feed.html')
    self.response.out.write(template.render(path, template_values))
      

# AdminMainHandler
# Main handler for the Admin section
# Displays all pages as a list

class AdminMainHandler(webapp.RequestHandler):
  def get(self):
    #Load site prefs
    site_prefs = get_site_prefs()
    
    q = Page.all()
    q.filter("owner =", None)
    q.order("title")
    pages = q.fetch(1000)
   
    #Render page
    template_values = {
        'site_title': site_prefs['title'],
        'description': site_prefs['description'],
        'pages': pages,
        'links': get_links(),
        'logouturl': users.create_logout_url("/"),
        'removed': self.request.get('removed') and True or False,
        'updated': self.request.get('updated') and True or False,
        'saved': self.request.get('saved') and self.request.get('saved') or False,
        'front':site_prefs['front'] or False
    }
    path = os.path.join(os.path.dirname(__file__), 'views/dashboard.html')
    self.response.out.write(template.render(path, template_values))

# AdminPublishHandler
# Publishes draft page

class AdminPublishHandler(webapp.RequestHandler):
  def get(self):
    key = self.request.get('key')
    try:
      page = Page.get(key)
    except:
      page = False
    if not page:
      return error_404()
    page.draft = False
    page.put()
    memcache.set("page-%s" % page.url, page)
    memcache.delete("site-links")
    memcache.delete("feed")
    self.redirect("/admin?published=%s" % key)

# AdminUnPublishHandler
# UnPublishes selected page by maiking it a draft

class AdminUnPublishHandler(webapp.RequestHandler):
  def get(self):
    key = self.request.get('key')
    try:
      page = Page.get(key)
    except:
      page = False
    if not page:
      return error_404()
    page.draft = True
    page.put()
    memcache.set("page-%s" % page.url, page)
    memcache.delete("site-links")
    memcache.delete("feed")
    self.redirect("/admin?unpublished=%s" % key)

# AdminRemoveHandler
# Deletes a page

class AdminRemoveHandler(webapp.RequestHandler):
  def get(self, url=False):
    if not url:
      return error_404()
    page = get_page(url);
    if not page:
      return error_404()
    if page.owner:
      memcache.delete('subpage-%s' % str(page.owner.key()))
      
    page.delete()
    memcache.delete("page-%s" % url)
    memcache.delete("site-links")
    memcache.delete("feed")
    self.redirect("/admin?removed=true")

# AdminEditHandler
# Add or edit an existing page

class AdminEditHandler(webapp.RequestHandler):
  def get(self, url=False):
    #Load site prefs
    site_prefs = get_site_prefs()
    
    page = False
    #Load current page
    if url:
      page = get_page(url)

    files = memcache.get('files')
    if files is None:
      files = []
      query = Media.all()
      query.order('-uploaded')
      f = query.fetch(1000)
      for file in f:
        files.append({
          'width': file.height,
          'height': file.width,
          'type':file.type,
          'key':str(file.key()),
          'name':file.name,
          'status':'OK',
          'description':file.description
        })
      if files:
        memcache.set('files', files)

    #Render page
    template_values = {
        'site_title': site_prefs['title'],
        'description': site_prefs['description'],
        'url': url,
        'owner': page and page.owner and str(page.owner.key()) or False,
        'draft': not page or page.draft,
        'page': page,
        'front':page and site_prefs['front']==page.url or False,
        'links': get_links(),
        'logouturl': users.create_logout_url("/"),
        'files': json.dumps(files)
    }
    path = os.path.join(os.path.dirname(__file__), 'views/edit.html')
    self.response.out.write(template.render(path, template_values))
    
  def post(self):
    key = self.request.get('key')
    title = self.request.get('title')
    url = self.request.get('url')
    
    # Remove all non-ascii characters from the URL 
    p  = re.compile(r'[^a-z\-0-9]', re.IGNORECASE)
    url = p.sub('', url)
    
    content = self.request.get('content')
    on_front = self.request.get('front') and True or False
    draft = self.request.get('draft') and not on_front and True or False
    owner = self.request.get('owner') or False
    
    page = False
    if len(key):
      try:
        page = Page.get(key)
      except:
        page = False

    memcache.delete('site-links')
    
    if not page:
      page = Page()
      page.url = get_unique_url(len(url) and url or u'page') # url is set at the first save
    

    page.title = title
    page.content = content
    page.draft = draft
    
    if page.owner and page.owner!=owner:
      memcache.delete('subpage-%s' % str(page.owner.key()))
    
    page.owner = owner and db.Key(owner) or None
    
    if page.owner:
      memcache.delete('subpage-%s' % str(page.owner.key()))
        
    page.put()
    memcache.set("page-%s" % page.url, page)
    memcache.delete('feed')
    
    if on_front:
      # Set to front page
      site_prefs = get_site_prefs()
      if not site_prefs['front'] or site_prefs['front']!= page.url:
        site_prefs['front'] = page.url
        set_site_prefs(site_prefs)
        memcache.delete("site-links")
    
    self.redirect("/admin?saved=%s" % str(page.key()))

# AdminSiteHandler
# Edit site settings

class AdminSiteHandler(webapp.RequestHandler):
  def get(self):
    #Load site prefs
    site_prefs = get_site_prefs()
    
    #Render page
    template_values = {
        'site_title': site_prefs['title'],
        'description': site_prefs['description'],
        'templateText': site_prefs['templateText'],
        'templateDefault': site_prefs['templateDefault'],
        'links': get_links(),
        'logouturl': users.create_logout_url("/")
    }
    path = os.path.join(os.path.dirname(__file__), 'views/site.html')
    self.response.out.write(template.render(path, template_values))
  def post(self):
    title = self.request.get('title')
    description = self.request.get('description')
    templateText = self.request.get('templateText')
    use_own_template = self.request.get('use_own_template') and True or False
    
    templateDefault = not use_own_template
    
    if not len(title):
      title = u'TurbineCMS'
      
    site_prefs = get_site_prefs()
    
    site_prefs['title'] = title
    site_prefs['description'] = description
    site_prefs['templateText'] = len(templateText) and templateText or False
    site_prefs['templateDefault'] = templateDefault
    
    set_site_prefs(site_prefs)
    
    self.redirect("/admin?updated=true")

# AdminUploadHandler
# Upload file

class AdminUploadHandler(webapp.RequestHandler):
  def post(self):

    template_values = {
        'status':'',
        'width': 0,
        'height': 0,
        'type':'',
        'key':'',
        'name':'',
        'description':''
    }
    
    if not self.request.get('file') or len(self.request.get('file'))>1024*1024:
      template_values['status'] = 'ERROR'
      path = os.path.join(os.path.dirname(__file__), 'views/upload_response.html')
      self.response.out.write(template.render(path, template_values))
      return

    media = Media()
    # strip out the path Internet Explorer provides with the filename
    media.name = self.request.params['file'].filename.encode('utf-8').split("\\").pop().decode('utf-8')
    media.description = self.request.get('description')

    try:
      img = images.Image(self.request.get('file'))
      width = img.width
      height = img.height
      
      media.type="IMAGE"
      
      if width>800 or height>600:
        img.resize(width=800, height=600)
        
      img.im_feeling_lucky()
      media.file = img.execute_transforms(output_encoding=images.JPEG)

      img = images.Image(media.file)
      media.width = img.width
      media.height = img.height
      
      img.resize(width=80, height=60)
      img.im_feeling_lucky()
      media.thumbnail = img.execute_transforms(output_encoding=images.JPEG)
    except:
      media.file = self.request.get('file')
      media.type="FILE"
      media.width = 0
      media.height = 0

    media.put()
    
    template_values['status'] = 'OK'
    template_values['type'] = media.type
    template_values['name'] = media.name
    template_values['width'] = media.width
    template_values['height'] = media.height
    template_values['description'] = media.description
    template_values['key'] = str(media.key())
    
    memcache.delete('files')
    path = os.path.join(os.path.dirname(__file__), 'views/upload_response.html')
    self.response.out.write(template.render(path, template_values))

# RemoveMedia
# Deletes selected file

class RemoveMedia(webapp.RequestHandler):
  def post(self):
    key = self.request.get('key')
    try:
      image = Media.get(key) 
    except:
      image = False
    if image:
      image.delete()
    
    memcache.delete('image_%s_%s' % ('full',key))
    memcache.delete('image_%s_%s' % ('thumb',key))
    memcache.delete('media_%s' % key)
    memcache.delete('files')
    
    self.response.out.write('deleted')

# ImageHandler
# Displays selected image in requested size (full size on thumbnail)

class ImageHandler(webapp.RequestHandler):
  def get(self, size, key, name=''):
    
    image = memcache.get('image_%s_%s' % (size,key))
    if image is None:
      try:
        image = Media.get(key) 
      except:
        image = False
      memcache.set('image_%s_%s' % (size,key), image)

    if image:
      self.response.headers['Content-Type'] = 'image/jpeg'
      self.response.out.write(size=='full' and image.file or image.thumbnail)
    else:
      return error_404(self)

# MediaHandler
# Forces download of selected file

class MediaHandler(webapp.RequestHandler):
  def get(self, key, name=''):
    
    media = memcache.get('media_%s' % key)
    if media is None:
      try:
        media = Media.get(key) 
      except:
        media = False
      memcache.set('media_%s' % key, media)

    if media:
      self.response.headers['Content-Type'] = 'application/octet-stream'
      self.response.headers['Content-disposition'] = 'attachment; filename="%s"' % str(media.name)
      self.response.out.write(media.file)
    else:
      return error_404(self)

def main():
  application = webapp.WSGIApplication([('/', PageHandler),
                                        (r'/page/(.*)', PageHandler),
                                        (r'/image/(.*)/(.*)/(.*)', ImageHandler),
                                        (r'/download/(.*)/(.*)', MediaHandler),
                                        ('/feed', FeedHandler),
                                        ('/admin/upload', AdminUploadHandler),
                                        ('/admin', AdminMainHandler),
                                        ('/admin/add', AdminEditHandler),
                                        ('/admin/site', AdminSiteHandler),
                                        ('/admin/edit', AdminEditHandler),
                                        ('/admin/remove-media', RemoveMedia),
                                        ('/admin/publish', AdminPublishHandler),
                                        ('/admin/unpublish', AdminUnPublishHandler),
                                        (r'/admin/edit/(.*)', AdminEditHandler),
                                        (r'/admin/remove/(.*)', AdminRemoveHandler)
                                        ],
                                       debug=True)
  wsgiref.handlers.CGIHandler().run(application)


if __name__ == '__main__':
  main()