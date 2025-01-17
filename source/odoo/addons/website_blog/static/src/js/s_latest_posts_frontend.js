odoo.define('website_blog.s_latest_posts_frontend', function (require) {
'use strict';

var core = require('web.core');
var publicWidget = require('web.public.widget');

var _t = core._t;

publicWidget.registry.js_get_posts = publicWidget.Widget.extend({
    selector : '.js_get_posts',
    disabledInEditableMode: false,

    /**
     * @override
     */
    start: function () {
        var self = this;
        var limit = self.$target.data('postsLimit') || 3;
        var blogID = self.$target.data('filterByBlogId');
        var template = self.$target.data('template') || 'website_blog.s_latest_posts_list_template';
        var loading = self.$target.data('loading');

        this.$target.empty(); // Compatibility with db that saved content inside by mistake
        this.$target.attr('contenteditable', 'False'); // Prevent user edition

        var domain = [
            ['website_published', '=', true],
            ['post_date', '<=', moment().utc().locale('en').format('YYYY-MM-DD HH:mm:ss')],
        ];
        if (blogID) {
            domain.push(['blog_id', '=', parseInt(blogID)]);
        }

        var prom = new Promise(function (resolve) {
            self._rpc({
                route: '/blog/render_latest_posts',
                params: {
                    template: template,
                    domain: domain,
                    limit: limit,
                },
            }).then(function (posts) {
                var $posts = $(posts).filter('.s_latest_posts_post');
                if (!$posts.length) {
                    self.$target.append($('<div/>', {class: 'col-md-6 offset-md-3'})
                    .append($('<div/>', {
                        class: 'alert alert-warning alert-dismissible text-center',
                        text: _t("No blog post was found. Make sure your posts are published."),
                    })));
                    return;
                }

                if (loading && loading === true) {
                    // Perform an intro animation
                    self._showLoading($posts);
                } else {
                    self.$target.html($posts);
                }
                resolve();
            }).guardedCatch(function () {
                if (self.editableMode) {
                    self.$target.append($('<p/>', {
                        class: 'text-danger',
                        text: _t("An error occured with this latest posts block. If the problem persists, please consider deleting it and adding a new one"),
                    }));
                }
                resolve();
            });
        });
        return Promise.all([this._super.apply(this, arguments), prom]);
    },
    /**
     * @override
     */
    destroy: function () {
        this.$target.empty();
        this._super.apply(this, arguments);
    },

    //--------------------------------------------------------------------------
    // Private
    //--------------------------------------------------------------------------

    /**
     * @private
     */
    _showLoading: function ($posts) {
        var self = this;

        _.each($posts, function (post) {
            var $post = $(post);
            var $loadingContainer = $post.find('.loading_container');
            var $thumb = $post.find('.thumb .o_blog_cover_image');
            var $progress = $('<div/>', {
                class: 'progress js-loading',
            }).append($('<div/>', {
                class: 'progress-bar',
                role: 'progressbar',
                'aria-valuenow': '0',
                'aria-valuemin': '0',
                'aria-valuemax': '100',
                css: {
                    width: 0,
                },
            }));

            // If can't find loading container or thumb inside the post, then they are the post itself
            if (!$loadingContainer.length) {
                $loadingContainer = $post;
            }
            if (!$thumb.length)  {
                $thumb = $post;
            }

            $post.addClass('js-loading');
            $progress.appendTo($loadingContainer);
            $post.appendTo(self.$target);

            var m = $thumb.css('background-image').match(/url\(["']?(.+)["']?\)/);
            var bg = m ? m[1] : 'none';
            var loaded = false;

            var $bar = $progress.find('.progress-bar');
            $bar.css('width', '50%').attr('aria-valuenow', '50');

            var $dummyImg = $('<img/>');

            // Show the post after 5sec in any case
            var timer = setTimeout(function () {
                $dummyImg.remove();
                $post.removeClass('js-loading');
                $progress.hide();
            }, 5000);

            $dummyImg.on('load', function () {
                $bar.css('width', '100%').attr('aria-valuenow', '100');
                setTimeout(function () {
                    $post.removeClass('js-loading');
                    $progress.fadeOut(500);
                }, 500);
                $dummyImg.remove();
                clearTimeout(timer);
            });

            $dummyImg.attr('src', bg);
        });
    },
});
});
